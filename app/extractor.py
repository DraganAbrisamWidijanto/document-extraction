"""app/extractor.py — VLM extraction pipeline (clean, modular).

Components:
  Config        — dataclass from env vars
  KeyPool       — thread-safe round-robin over API keys (read from geminiapi.txt)
  call_vlm()    — one OpenAI-compatible request with retry + key rotation
  extract_receipt() — full pipeline: downscale -> call -> parse -> validate -> cost

Provider: Gemini via its OpenAI-compatible endpoint
(base_url .../v1beta/openai). Any OpenAI-compatible VLM works via env vars.

Config (env):
  VLM_BASE_URL      default https://generativelanguage.googleapis.com/v1beta/openai
  VLM_MODEL         default gemini-3.5-flash-lite (free tier)
  VLM_API_KEY_FILE  default <repo>/geminiapi.txt (one key per line, all rotated)
  VLM_API_KEY       single-key override (docker/CI)
  VLM_TIMEOUT_S     default 180
  VLM_MAX_TOKENS    default 3000
  VLM_RETRIES       default 3
  VLM_PRICE_INPUT_PER_M  USD per 1M input tokens  (0 = free tier)
  VLM_PRICE_OUTPUT_PER_M USD per 1M output tokens (0 = free tier)
"""
import base64
import io
import itertools
import json
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_KEY_FILE = os.path.join(BASE_DIR, "geminiapi.txt")

# ---------------------------------------------------------------------------
# Schema prompt (v3) — includes per-field confidence
# ---------------------------------------------------------------------------
SCHEMA_PROMPT = """You are a document extraction system. Extract structured data from the receipt image.

Return ONLY valid JSON (no markdown fences, no commentary) with this exact schema:

{
  "menu_items": [
    {
      "name": "item name as printed",
      "quantity": <number or null>,
      "unit_price": <number or null>,
      "amount": <line total or null>,
      "confidence": {"name": 0-1, "quantity": 0-1, "unit_price": 0-1, "amount": 0-1}
    }
  ],
  "subtotal": <number or null>,
  "discount": <number or null>,
  "tax": <number or null>,
  "service_charge": <number or null>,
  "total": <number or null>,
  "confidence": {
    "subtotal": 0-1, "discount": 0-1, "tax": 0-1,
    "service_charge": 0-1, "total": 0-1,
    "payment.method": 0-1, "payment.amount_paid": 0-1, "payment.change": 0-1
  },
  "payment": {
    "method": "cash" | "credit_card" | "emoney" | "debit" | "transfer" | "other" | null,
    "amount_paid": <number or null>,
    "change": <number or null>
  }
}

IMPORTANT RULES:

- The receipt image is the source of truth.
- If a field is not present in the image and cannot be unambiguously derived according to the rules below, use null. NEVER invent values.
- If a value is ambiguous, illegible, or its semantic meaning is unclear, use null instead of guessing.
- null means the field is absent or cannot be reliably determined. 0 means the field is explicitly printed as 0. Do NOT turn absence into 0, and do NOT turn an explicitly printed 0 into null.

- Extract only information relevant to the defined schema. Ignore non-schema text such as greetings, thank-you messages, advertisements, and customer-service messages.

- Distinguish between readable text and its semantic meaning. A readable number must not be assigned to a field merely because it looks plausible.

- Distinguish between intermediate totals and the final amount payable. If multiple "Total" values appear, determine which one represents the final payable/grand total based on the receipt structure.
- When in doubt, the final total equals the amount actually paid: if a separate payment amount (amount_paid) is printed and it differs from an earlier "Total" line, the final total is the amount_paid value (e.g. NASGOR: "Total 193.638" + "P.Rest 10 19.364" + "Debit 213.002" → total = 213.002, because 193.638 + 19.364 = 213.002).

- Validate arithmetic relationships as consistency checks, but never use an assumed formula to override clearly printed values.

- Tax may be tax-INCLUSIVE or tax-EXCLUSIVE. Do NOT assume subtotal + tax = total.
  - If the receipt shows DPP + tax = total while line-item prices sum to total, treat the tax as tax-inclusive.
  - If the receipt shows subtotal + separate tax = grand total, treat the tax as tax-exclusive.
  - Use the printed line structure to determine the relationship.

- Do not confuse DPP/taxable base with subtotal.

- For subtotal:
  - If explicitly printed, use the printed value.
  - If not explicitly printed but can be unambiguously determined from line-item amounts, compute it from the line items.
  - Do not compute it when receipt-specific calculations make the relationship ambiguous.

- For line items:
  - Every purchased item is one entry, in printed order.
  - Verify quantity × unit_price = amount when possible.
  - If quantity > 1 and only the line amount is printed, compute unit_price = amount / quantity.
  - If quantity = 1, unit_price equals the line amount.
  - NEVER put a line total into unit_price.
  - Do NOT treat discount/adjustment lines (e.g. "DISC", "DISC ITEM", "DISCOUNT") as menu items.
  - A package/bundle sold as one line (e.g. "1 SET PAKET MERONCE" with its components listed below it) is ONE menu item. Do NOT list its components as separate menu items.

- For payment:
  - amount_paid is the amount actually tendered/paid.
  - change is the explicitly printed change/kembalian.
  - If change is not printed, use null.
  - If Change = 0 is explicitly printed, use 0.
  - Do not infer payment method from the amount; use the explicitly indicated payment method.
  - If amount_paid equals total and no change line is printed, change is still null — do not output 0.

- Do not silently reconcile conflicting printed values. If the receipt contains a genuine unresolved contradiction, use null for the affected field.

- Use plain integers (no decimal points, no fractions, no "Rp", no thousand separators, no currency symbols). E.g. 25000 not "25.000" or "Rp 25,000" or 25.0.
- All monetary values must be JSON integers representing the full amount in IDR. Never output floats or decimals for monetary fields.

- payment.method must be one of: "cash", "credit_card", "emoney", "debit", "transfer", "other", or null. "QRIS"/"qris" counts as "emoney".
- Do not infer a specific card type (debit/credit) from a generic payment terminal reference. "EDC", "Bayar EDC", or similar wording alone does not indicate debit or credit card. If the receipt only shows EDC, use null for payment.method rather than guessing.

- Preserve the product name as printed on its own line. Do not append variant, modifier, size, or option information from a separate line into the product name. For example, if the receipt prints "Set Roka Chizu" on one line and "Varian: Mild" on the next, the name is "Set Roka Chizu", not "Set Roka Chizu + Varian: Mild".
- However, a modifier line that appears below a menu item (e.g. "Ice" after a drink name, "Varian: ..." after a food name) is NOT a separate menu item — it describes the preceding item. Do not create extra menu items from modifier lines.

- confidence: how sure you are that the extracted value is correct, 0.0 to 1.0.
  Set confidence 0.0 for any field you set to null (abstained). Lower confidence when the print is blurry,
  cut off, or ambiguous. An absent/illegible field MUST be null with confidence 0.0 — never guess.
"""

SCALAR_FIELDS = ["subtotal", "discount", "tax", "service_charge", "total"]
PAY_FIELDS = ["method", "amount_paid", "change"]
ITEM_FIELDS = ["quantity", "unit_price", "amount"]
PAY_METHODS = {"cash", "credit_card", "emoney", "debit", "transfer", "other"}
TOP_KEYS = {"menu_items", "subtotal", "discount", "tax", "service_charge", "total", "payment", "confidence"}
ITEM_KEYS = {"name", "quantity", "unit_price", "amount", "confidence"}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@dataclass
class Config:
    base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    model: str = "gemini-3.5-flash-lite"
    timeout_s: int = 180
    max_tokens: int = 3000
    retries: int = 3
    price_input_per_m: float = 0.0
    price_output_per_m: float = 0.0
    keys: list = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Config":
        keys = load_api_keys()
        return cls(
            base_url=os.environ.get("VLM_BASE_URL", cls.base_url),
            model=os.environ.get("VLM_MODEL", cls.model),
            timeout_s=int(os.environ.get("VLM_TIMEOUT_S", str(cls.timeout_s))),
            max_tokens=int(os.environ.get("VLM_MAX_TOKENS", str(cls.max_tokens))),
            retries=int(os.environ.get("VLM_RETRIES", str(cls.retries))),
            price_input_per_m=float(os.environ.get("VLM_PRICE_INPUT_PER_M", "0")),
            price_output_per_m=float(os.environ.get("VLM_PRICE_OUTPUT_PER_M", "0")),
            keys=keys,
        )


def load_api_keys(path: Optional[str] = None) -> list:
    """Read API keys from file (one per line). Env VLM_API_KEY overrides/adds one."""
    keys: list = []
    file = path or os.environ.get("VLM_API_KEY_FILE") or DEFAULT_KEY_FILE
    if os.path.isfile(file):
        with open(file) as f:
            for line in f:
                k = line.strip()
                if k and not k.startswith("#") and k not in keys:
                    keys.append(k)
    env_key = os.environ.get("VLM_API_KEY")
    if env_key and env_key not in keys:
        keys.append(env_key)
    return keys


# ---------------------------------------------------------------------------
# Key pool (round-robin, thread-safe)
# ---------------------------------------------------------------------------
class KeyPool:
    """Round-robin over a list of API keys. Thread-safe via lock."""

    def __init__(self, keys: list):
        self._keys = list(keys)
        self._iter = itertools.cycle(self._keys) if self._keys else iter([])
        self._lock = threading.Lock()

    def next(self) -> Optional[str]:
        with self._lock:
            try:
                return next(self._iter)
            except StopIteration:
                return None

    def __len__(self):
        return len(self._keys)


# ---------------------------------------------------------------------------
# Image handling
# ---------------------------------------------------------------------------
def downscale(image_bytes: bytes, max_dim: int = 1600, quality: int = 85) -> bytes:
    """Resize (LANCZOS) if larger than max_dim and re-encode JPEG. Returns new bytes."""
    im = Image.open(io.BytesIO(image_bytes))
    w, h = im.size
    if w > max_dim or h > max_dim:
        ratio = min(max_dim / w, max_dim / h)
        im = im.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def image_to_data_url(image_bytes: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"


# ---------------------------------------------------------------------------
# VLM call
# ---------------------------------------------------------------------------
def _build_body(image_bytes: bytes, prompt: str, mime: str, cfg: Config) -> dict:
    return {
        "model": cfg.model,
        "stream": False,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_to_data_url(image_bytes, mime)}},
            ],
        }],
        "max_tokens": cfg.max_tokens,
    }


def _http_error_text(e: urllib.error.HTTPError) -> str:
    try:
        return e.read().decode()[:200]
    except Exception:
        return str(e)


def call_vlm(image_bytes: bytes, prompt: str, cfg: Config, mime: str = "image/png") -> dict:
    """One extraction call with retry + key rotation.

    Returns: {"raw": str, "latency_s": float, "ok": bool,
              "usage": dict|None, "key_used": str|None}
    On 429/402/403/5xx it rotates to the next key (backoff 30s x attempt).
    """
    pool = KeyPool(cfg.keys)
    body = _build_body(image_bytes, prompt, mime, cfg)
    t0 = time.time()
    for attempt in range(cfg.retries):
        key = pool.next()
        if key is None:
            return {"raw": "", "latency_s": time.time() - t0, "ok": False,
                    "usage": None, "key_used": None}
        req = urllib.request.Request(
            cfg.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=cfg.timeout_s) as resp:
                out = json.loads(resp.read().decode())
            content = out["choices"][0]["message"]["content"] or ""
            return {"raw": content, "latency_s": time.time() - t0, "ok": True,
                    "usage": out.get("usage"), "key_used": key}
        except urllib.error.HTTPError as e:
            if e.code in (429, 402, 403, 500, 502, 503):
                time.sleep(30 * (attempt + 1))  # rotate key next attempt
            else:
                return {"raw": "", "latency_s": time.time() - t0, "ok": False,
                        "usage": None, "key_used": key,
                        "error": f"HTTP {e.code}: {_http_error_text(e)}"}
        except Exception:
            if attempt < cfg.retries - 1:
                time.sleep(15)
    return {"raw": "", "latency_s": time.time() - t0, "ok": False, "usage": None, "key_used": None}


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------
def parse_json_response(text: str) -> dict:
    """Extract JSON from model response (handles markdown fences)."""
    if not text:
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass
    return {}


# ---------------------------------------------------------------------------
# Schema validation (own validator — typed output, no invented fields)
# ---------------------------------------------------------------------------
def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_prediction(pred: dict) -> tuple:
    """Validate prediction against schema. Returns (ok: bool, errors: list[str])."""
    errors = []
    if not isinstance(pred, dict):
        return False, ["prediction is not a dict"]

    unknown = set(pred) - TOP_KEYS
    if unknown:
        errors.append(f"unknown top-level keys: {sorted(unknown)}")

    items = pred.get("menu_items")
    if items is None:
        errors.append("missing 'menu_items'")
    elif not isinstance(items, list):
        errors.append("'menu_items' must be a list")
    else:
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                errors.append(f"item {i} is not a dict"); continue
            unk = set(it) - ITEM_KEYS
            if unk:
                errors.append(f"item {i} unknown keys: {sorted(unk)}")
            if not isinstance(it.get("name"), str) or not it["name"].strip():
                errors.append(f"item {i} 'name' missing/not str")
            for f in ITEM_FIELDS:
                v = it.get(f)
                if v is not None and not _is_num(v):
                    errors.append(f"item {i}.{f} not a number: {v!r}")
            conf = it.get("confidence")
            if conf is not None and (not isinstance(conf, dict) or
                                     any(not _is_num(c) or not (0 <= c <= 1) for c in conf.values())):
                errors.append(f"item {i} 'confidence' invalid")

    for f in SCALAR_FIELDS:
        v = pred.get(f)
        if v is not None and not _is_num(v):
            errors.append(f"'{f}' not a number: {v!r}")
        # reject float for monetary fields (must be int or null)
        if v is not None and isinstance(v, float):
            errors.append(f"'{f}' must be integer, got float: {v!r}")

    pay = pred.get("payment")
    if pay is None:
        errors.append("missing 'payment'")
    elif not isinstance(pay, dict):
        errors.append("'payment' must be a dict")
    else:
        m = pay.get("method")
        if m is not None and m not in PAY_METHODS:
            errors.append(f"payment.method invalid: {m!r}")
        for f in ("amount_paid", "change"):
            v = pay.get(f)
            if v is not None and not _is_num(v):
                errors.append(f"payment.{f} not a number: {v!r}")
            # reject float for monetary fields
            if v is not None and isinstance(v, float):
                errors.append(f"payment.{f} must be integer, got float: {v!r}")

    conf = pred.get("confidence")
    if conf is not None and (not isinstance(conf, dict) or
                             any(not _is_num(c) or not (0 <= c <= 1) for c in conf.values())):
        errors.append("'confidence' must be dict of numbers in [0,1]")

    return (len(errors) == 0), errors


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------
def compute_cost(usage: Optional[dict], cfg: Config) -> dict:
    """usage: {prompt_tokens, completion_tokens} -> USD cost + token counts."""
    if not usage:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
    p, c = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
    cost = p / 1e6 * cfg.price_input_per_m + c / 1e6 * cfg.price_output_per_m
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c,
            "cost_usd": round(cost, 6)}


# ---------------------------------------------------------------------------
# Top-level API
# ---------------------------------------------------------------------------
def extract_receipt(image_bytes: bytes, mime: str = "image/png", cfg: Optional[Config] = None) -> dict:
    """Full pipeline: downscale -> VLM -> parse -> validate -> cost.

    Returns: {prediction, confidence, latency_s, raw, ok, usage, cost_usd,
              validation, downscaled}
    """
    cfg = cfg or Config.from_env()
    # Downscale large images (measured optimization: -35% latency, 0 timeouts)
    orig_size = len(image_bytes)
    downscaled = False
    if orig_size > 1_000_000:  # >1 MB
        smaller = downscale(image_bytes)
        if len(smaller) < orig_size:
            image_bytes, downscaled = smaller, True
            mime = "image/jpeg"

    res = call_vlm(image_bytes, SCHEMA_PROMPT, cfg, mime)
    pred = parse_json_response(res["raw"])
    valid, errors = validate_prediction(pred)
    cost = compute_cost(res["usage"], cfg)

    return {
        "prediction": pred,
        "latency_s": round(res["latency_s"], 2),
        "raw": res["raw"],
        "ok": res["ok"] and bool(pred),
        "usage": res["usage"],
        "cost_usd": cost["cost_usd"],
        "tokens": cost,
        "validation": {"valid": valid, "errors": errors},
        "downscaled": downscaled,
    }


def extract_many(items: list, cfg: Config, workers: int = 4, show: bool = True) -> dict:
    """Concurrent batch extraction.

    items: list of dicts {name, path, mime}
    Returns dict name -> extract_receipt result. Thread-safe key pool inside call_vlm.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def one(item):
        with open(item["path"], "rb") as f:
            data = f.read()
        if show:
            print(f"[{item['name']}] extracting...", flush=True)
        t0 = time.time()
        result = extract_receipt(data, item.get("mime", "image/png"), cfg)
        result["name"] = item["name"]
        if show:
            pred = result["prediction"]
            print(f"    {result['latency_s']:.1f}s ok={result['ok']} "
                  f"total={pred.get('total') if pred else None} "
                  f"items={len(pred.get('menu_items') or []) if pred else 0}", flush=True)
        return result

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
