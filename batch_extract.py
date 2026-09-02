"""Batch extraction for eval test set: 44 CORD (cord_pool) + 5 self-scan + 5 Kleister PDF.

Usage:
  python batch_extract.py [--source cord|self|kleister|all] [--limit N] [--workers 4]
Output: data/predictions/pred_<name>.json (raw + prediction + usage + cost)
Concurrent: ThreadPoolExecutor with round-robin API key pool.

With --api-url, extraction goes through the FastAPI /extract or /extract-pdf
endpoint instead of the in-process pipeline (end-to-end eval against the
running API):
  python batch_extract.py --source all --api-url http://localhost:8000
"""
import argparse
import json
import os
import time
import urllib.request
import uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "data", "predictions")
os.makedirs(OUT_DIR, exist_ok=True)

from app.extractor import Config, extract_many
from app.pdf_extractor import extract_many_pdf


def load_manifest(source):
    """Return list of {name, path, mime, kind}."""
    items = []
    if source in ("cord", "all"):
        m = json.load(open(os.path.join(BASE_DIR, "data", "cord_pool", "manifest_test.json")))
        for e in m:
            for d in ("data/cord_pool",):
                p = os.path.join(BASE_DIR, d, e["image"])
                if os.path.exists(p):
                    items.append({"name": f"cord_{e['idx']:03d}", "path": p,
                                  "mime": "image/png", "kind": "image"})
                    break
    if source in ("self", "all"):
        m = json.load(open(os.path.join(BASE_DIR, "data", "self_scan", "manifest.json")))
        for e in m:
            p = os.path.join(BASE_DIR, "data", "self_scan", e["image"])
            items.append({"name": e["idx"], "path": p, "mime": "image/jpeg", "kind": "image"})
    if source in ("kleister", "all"):
        m = json.load(open(os.path.join(BASE_DIR, "data", "kleister", "manifest.json")))
        for e in m:
            p = os.path.join(BASE_DIR, "data", "kleister", "documents", e["image"])
            items.append({"name": e["idx"], "path": p, "mime": "application/pdf", "kind": "pdf"})
    return items


def _extract_via_api_once(item, api_url, timeout_s=600):
    """POST one file (image or PDF) to the FastAPI endpoint."""
    with open(item["path"], "rb") as f:
        data = f.read()
    endpoint = "/extract-pdf" if item.get("kind") == "pdf" else "/extract"
    boundary = uuid.uuid4().hex
    # preserve the real filename+extension so server-side PDF checks pass
    filename = os.path.basename(item["path"])
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {item.get('mime', 'image/png')}\r\n\r\n"
            ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{api_url}{endpoint}", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.load(resp)


def extract_via_api(item, api_url, timeout_s=600):
    """POST one file (image or PDF) to the FastAPI endpoint; one retry on
    transient errors (startup race, dropped connection)."""
    try:
        return _extract_via_api_once(item, api_url, timeout_s)
    except Exception:
        time.sleep(5)
        return _extract_via_api_once(item, api_url, timeout_s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="all", choices=["cord", "self", "kleister", "all"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--overwrite", action="store_true", help="re-extract existing preds")
    ap.add_argument("--api-url", default=None,
                    help="extract via HTTP API at this base URL (e.g. http://api:8000) "
                         "instead of the in-process pipeline")
    args = ap.parse_args()

    items = load_manifest(args.source)
    if args.limit:
        items = items[:args.limit]
    if not args.overwrite:
        todo, skipped = [], 0
        for it in items:
            out_path = os.path.join(OUT_DIR, f"pred_{it['name']}.json")
            if os.path.exists(out_path):
                skipped += 1
            else:
                todo.append(it)
        items = todo
        print(f"skip {skipped} existing, {len(items)} to extract")
    print(f"Total to extract: {len(items)}  (workers={args.workers}"
          + (f", via API {args.api_url}" if args.api_url else ", in-process") + ")")

    if args.api_url:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        cfg = Config.from_env()
        results = {}
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(extract_via_api, it, args.api_url): it["name"] for it in items}
            for fut in as_completed(futs):
                name = futs[fut]
                try:
                    res = fut.result()
                    results[name] = {
                        "name": name, "model": cfg.model,
                        "latency_s": res.get("latency_s"),
                        "raw": res.get("raw", ""), "prediction": res.get("prediction", {}),
                        "confidence": res.get("confidence"),  # per-field 0-1 (Kleister PDF)
                        "usage": res.get("usage"),
                        "cost_usd": res.get("cost_usd"), "validation": res.get("validation"),
                    }
                except Exception as e:
                    results[name] = {"name": name, "ok": False, "error": str(e),
                                     "latency_s": 0, "prediction": {}, "raw": ""}
    else:
        cfg = Config.from_env()
        img_items = [it for it in items if it.get("kind") != "pdf"]
        pdf_items = [it for it in items if it.get("kind") == "pdf"]
        results = {}
        if img_items:
            results.update(extract_many(img_items, cfg, workers=args.workers))
        if pdf_items:
            results.update(extract_many_pdf(pdf_items, cfg, workers=min(args.workers, 3)))

    for name, res in results.items():
        out_path = os.path.join(OUT_DIR, f"pred_{name}.json")
        record = {
            "name": name, "image": os.path.basename(res.get("path", "")), "model": cfg.model,
            "ok": res.get("ok", True), "error": res.get("error"),
            "latency_s": res.get("latency_s"), "raw": res.get("raw", ""),
            "prediction": res.get("prediction", {}), "confidence": res.get("confidence"),
            "usage": res.get("usage"),
            "cost_usd": res.get("cost_usd"), "downscaled": res.get("downscaled", False),
            "validation": res.get("validation"),
            "pages_total": res.get("pages_total"),
            "pages_selected": res.get("pages_selected"),
            "page_indices": res.get("page_indices"),
        }
        with open(out_path, "w") as f:
            json.dump(record, f, indent=2)

    ok = sum(1 for r in results.values() if r.get("prediction"))
    total_cost = sum((r.get("cost_usd") or 0) for r in results.values())
    print(f"\n=== DONE === ok={ok}/{len(results)}  cost=${total_cost:.6f}")


if __name__ == "__main__":
    main()