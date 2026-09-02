"""app/pdf_extractor.py — multi-page PDF extraction pipeline.

Pipeline: PDF -> text per page -> keyword scoring -> select pages -> render ->
VLM per page -> merge across pages -> validate -> cost.

Local, free: text extraction + page selection use PyMuPDF (no API calls);
only the selected pages go to the VLM (Gemini free tier via app.extractor).

Top-level API:
  extract_kleister_pdf(pdf_path, cfg) -> dict  (same shape as extract_receipt)
"""
import json
import os
import re
import time
from typing import Optional

import pymupdf  # PyMuPDF (also importable as fitz)

from app.extractor import Config, call_vlm, compute_cost, downscale, parse_json_response
from app.kleister_prompt import (
    KLEISTER_FIELDS,
    KLEISTER_NUMERIC,
    KLEISTER_PROMPT,
    PAGE_SCORE_KEYWORDS,
)

MAX_PAGES = 7      # cap on selected pages per document
MIN_PAGES = 3      # floor: page 1 + at least this many scored pages if available
PAGE_RENDER_DPI = 150

# Regex patterns for scoring boost
POSTCODE_RE = re.compile(r'\b[A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}\b')
CHARITY_NO_RE = re.compile(r'\b(charity|registered)\s*(?:no\.?|number|reg(?:\.|istered)?)\s*:?\s*\d{3,}\b', re.I)
COMPANY_GUARANTEE_RE = re.compile(r'\bcompany\s+limited\s+by\s+guarantee\b', re.I)


# ---------------------------------------------------------------------------
# Text extraction + page selection (local, free)
# ---------------------------------------------------------------------------
def pdf_to_text_per_page(pdf_path: str) -> list:
    """Return list of per-page text strings ('' for image-only pages)."""
    texts = []
    with pymupdf.open(pdf_path) as doc:
        for page in doc:
            texts.append(page.get_text() or "")
    return texts


def _is_sofa_page(tl: str) -> bool:
    """True for a Statement of Financial Activities totals page (income+spending)."""
    return "total income" in tl and ("total expenditure" in tl or "resources expended" in tl)


def score_pages(texts: list) -> list:
    """Score each page by keyword hits across field groups + regex boosts."""
    scores = []
    for t in texts:
        tl = t.lower()
        score = 0
        for group_kws in PAGE_SCORE_KEYWORDS.values():
            score += sum(1 for kw in group_kws if kw in tl)
        # regex boosts: postcode / charity number / company limited by guarantee
        # catch address & legal blocks that pure keywords miss
        score += len(POSTCODE_RE.findall(t)) * 5
        score += len(CHARITY_NO_RE.findall(t)) * 5
        score += len(COMPANY_GUARANTEE_RE.findall(t)) * 3
        if "registered office" in tl:
            score += 3
        # SoFA totals page: carries the exact annual income/spending figures
        if _is_sofa_page(tl):
            score += 4
        scores.append(score)
    return scores


def best_sofa_page(texts: list, scores: list) -> Optional[int]:
    """The SoFA totals page carrying the actual figures, or None when the text
    layer has none (image-only scans).

    Ranking: several pages can carry SoFA-like keywords (fund accounts, notes,
    comparative tables); the totals table is the one densest with
    thousands-separated numbers ('57,333'). Tie-break on page score, then
    earliest page."""
    best, best_key = None, None
    for i, t in enumerate(texts):
        tl = (t or "").lower()
        if not _is_sofa_page(tl):
            continue
        big_nums = len(re.findall(r"\d{1,3},\d{3}\b", t or ""))
        key = (big_nums, scores[i], -i)
        if best is None or key > best_key:
            best, best_key = i, key
    return best


def select_pages(scores: list, max_pages: int = MAX_PAGES) -> list:
    """Pick page indices for VLM rendering.

    Guarantees: page 0 (cover) and the LAST page (legal/address block — charity
    number, registered office, postcode almost always live there) are always
    included. Remaining slots fill by score, then by even spread. Returns
    sorted 0-based indices, at most max_pages.
    """
    n = len(scores)
    if n == 0:
        return []
    if n == 1:
        return [0]

    selected = {0, n - 1}
    scored = [(s, i) for i, s in enumerate(scores) if i not in selected]
    scored.sort(key=lambda x: (-x[0], x[1]))
    for s, i in scored:
        if len(selected) >= max_pages:
            break
        selected.add(i)

    # Uniformly-low scoring: ensure MIN_PAGES by sampling evenly.
    if len(selected) < min(MIN_PAGES, n):
        step = max(1, n // min(MIN_PAGES, n))
        for i in range(1, n, step):
            if len(selected) >= min(MIN_PAGES, n):
                break
            selected.add(i)

    return sorted(selected)


def render_pages(pdf_path: str, page_indices: list) -> dict:
    """Render selected pages to downscaled JPEG bytes.

    Returns {idx: {"png": bytes, "jpeg": bytes, "mime": str}}. JPEG used for the
    VLM call (smaller/faster); PNG kept for reference if needed.
    """
    out = {}
    with pymupdf.open(pdf_path) as doc:
        for i in page_indices:
            if i >= len(doc):
                continue
            pix = doc[i].get_pixmap(dpi=PAGE_RENDER_DPI)
            png = pix.tobytes("png")
            jpeg = downscale(png)  # re-encode to JPEG, resize if huge
            out[i] = {"png": png, "jpeg": jpeg, "mime": "image/jpeg"}
    return out


# ---------------------------------------------------------------------------
# Merging per-page results
# ---------------------------------------------------------------------------
def _norm_str(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def merge_pages(page_results: list) -> dict:
    """Merge per-page {prediction, confidence} into one field-wise result.

    Rule: for each field take the non-null value with the highest per-page
    confidence. If a second distinct value appears with confidence within
    0.15 of the best (and > 0.5), treat as conflict -> halve final confidence.
    Numeric fields: compare numerically after normalization.
    """
    merged, conf = {}, {}
    for field in KLEISTER_FIELDS:
        cands = []  # (confidence, value)
        for pr in page_results:
            pred, c = pr.get("prediction") or {}, pr.get("confidence") or {}
            v = pred.get(field)
            if v is None:
                continue
            if field in KLEISTER_NUMERIC:
                try:
                    v = int(float(v))
                except (TypeError, ValueError):
                    continue
            else:
                v = _norm_str(v)
                if v is None:
                    continue
            cands.append((float(c.get(field, 0.0) or 0.0), v))
        if not cands:
            merged[field], conf[field] = None, 0.0
            continue
        cands.sort(key=lambda x: -x[0])
        best_c, best_v = cands[0]
        conflict = False
        for c, v in cands[1:]:
            if field in KLEISTER_NUMERIC:
                distinct = v != best_v
            else:
                distinct = _norm_str(v).lower() != _norm_str(best_v).lower()
            if distinct and c > 0.5 and best_c - c <= 0.15:
                conflict = True
                break
        merged[field] = best_v
        conf[field] = round(best_c * 0.5, 2) if conflict else round(best_c, 2)
    return merged, conf


# ---------------------------------------------------------------------------
# Validation (own validator — typed output, no invented fields)
# ---------------------------------------------------------------------------
def validate_kleister(pred: dict) -> tuple:
    """Return (ok, errors). Checks unknown keys + per-field types."""
    errors = []
    if not isinstance(pred, dict):
        return False, ["prediction is not a dict"]
    unknown = set(pred) - set(KLEISTER_FIELDS)
    if unknown:
        errors.append(f"unknown keys: {sorted(unknown)}")
    for f in KLEISTER_FIELDS:
        v = pred.get(f)
        if v is None:
            continue
        if f in KLEISTER_NUMERIC:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                errors.append(f"'{f}' must be a number, got {v!r}")
        elif not isinstance(v, str):
            errors.append(f"'{f}' must be a string, got {v!r}")
    return (len(errors) == 0), errors


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------
def extract_kleister_pdf(pdf_path: str, cfg: Optional[Config] = None) -> dict:
    """Full multi-page pipeline. Returns extract_receipt-shaped dict plus
    'pages_total'/'pages_selected'/'page_indices'."""
    cfg = cfg or Config.from_env()
    t0 = time.time()

    try:
        texts = pdf_to_text_per_page(pdf_path)
    except Exception as e:
        return {"prediction": {}, "latency_s": round(time.time() - t0, 2),
                "raw": "", "ok": False, "usage": None, "cost_usd": 0.0,
                "tokens": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "validation": {"valid": False, "errors": [f"pdf_to_text failed: {e}"]},
                "pages_total": 0, "pages_selected": 0, "page_indices": []}

    pages_total = len(texts)
    scores = score_pages(texts)
    pages = select_pages(scores)
    # the SoFA totals page (densest with thousands-separated figures) is the
    # source of exact income/spending — guarantee it a slot like page 0 / last
    # page, swapping out the lowest-scoring non-guaranteed page when full
    sofa = best_sofa_page(texts, scores)
    if sofa is not None and sofa not in pages:
        if len(pages) < MAX_PAGES:
            pages.append(sofa)
        else:
            cand = [i for i in pages if i not in (0, pages_total - 1, sofa)]
            if cand:
                lowest = min(cand, key=lambda i: (scores[i], i))
                pages[pages.index(lowest)] = sofa
    pages = sorted(set(pages))
    rendered = render_pages(pdf_path, pages)

    page_results = []
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    cost_total = 0.0
    for i in pages:
        if i not in rendered:
            continue
        img = rendered[i]["jpeg"]
        # Text layer as an aid for dense tables (image stays source of truth).
        page_text = texts[i][:4000] if i < len(texts) else ""
        prompt = KLEISTER_PROMPT
        if page_text.strip():
            prompt += ("\n\n--- PAGE TEXT LAYER (may be imperfect) — use it to "
                       f"verify figures, especially dense financial tables ---\n{page_text}")
        res = call_vlm(img, prompt, cfg, rendered[i]["mime"])
        pred = parse_json_response(res["raw"])
        usage = res.get("usage") or {}
        total_usage["prompt_tokens"] += int(usage.get("prompt_tokens", 0))
        total_usage["completion_tokens"] += int(usage.get("completion_tokens", 0))
        cost_total += compute_cost(usage, cfg)["cost_usd"]
        conf = pred.get("confidence") or {}
        page_results.append({"page": i, "prediction": pred, "confidence": conf,
                             "ok": res["ok"], "latency_s": res["latency_s"],
                             "raw": res["raw"]})
        if not res["ok"]:
            print(f"  [kleister page {i}] VLM call failed", flush=True)

    merged, conf = merge_pages(page_results)
    valid, errors = validate_kleister(merged)
    n_found = sum(1 for v in merged.values() if v is not None)
    ok = valid and n_found > 0

    return {
        "prediction": merged,
        "confidence": conf,
        "latency_s": round(time.time() - t0, 2),
        "raw": json.dumps([{"page": pr["page"], "raw": pr["raw"]} for pr in page_results]),
        "ok": ok,
        "usage": {"prompt_tokens": total_usage["prompt_tokens"],
                  "completion_tokens": total_usage["completion_tokens"]},
        "cost_usd": round(cost_total, 6),
        "tokens": {"prompt_tokens": total_usage["prompt_tokens"],
                   "completion_tokens": total_usage["completion_tokens"],
                   "total_tokens": total_usage["prompt_tokens"] + total_usage["completion_tokens"]},
        "validation": {"valid": valid, "errors": errors},
        "pages_total": pages_total,
        "pages_selected": len(pages),
        "page_indices": pages,
        "page_results": page_results,
    }


def extract_many_pdf(items: list, cfg: Config, workers: int = 3, show: bool = True) -> dict:
    """Concurrent multi-page PDF extraction. items: [{name, path}]."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def one(item):
        if show:
            print(f"[{item['name']}] extracting...", flush=True)
        r = extract_kleister_pdf(item["path"], cfg)
        r["name"] = item["name"]
        if show:
            print(f"    {r['latency_s']:.1f}s ok={r['ok']} pages {r['pages_selected']}/{r['pages_total']} "
                  f"charity={r['prediction'].get('charity_name')}", flush=True)
        return r

    results = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, it): it["name"] for it in items}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                results[name] = fut.result()
            except Exception as e:
                results[name] = {"name": name, "ok": False, "error": str(e),
                                 "latency_s": 0, "prediction": {}, "raw": ""}
    return results
