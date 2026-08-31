"""Shared extraction logic: VLM call via OpenAI-compatible endpoint + schema prompt.

Config via env vars (see README):
  VLM_BASE_URL   default http://127.0.0.1:20128/v1
  VLM_API_KEY    default: read from 9Router SQLite (local dev only)
  VLM_MODEL      default bai/mimo-v2.5
  VLM_TIMEOUT_S  default 180
"""
import os, json, base64, sqlite3, time, urllib.request, urllib.error

SCHEMA_PROMPT = """You are a document extraction system. Extract structured data from the receipt image.

Return ONLY valid JSON (no markdown fences, no commentary) with this exact schema:

{
  "menu_items": [
    {
      "name": "item name as printed",
      "quantity": <number or null>,
      "unit_price": <number or null>,
      "amount": <line total or null>
    }
  ],
  "subtotal": <number or null>,
  "discount": <number or null>,
  "tax": <number or null>,
  "service_charge": <number or null>,
  "total": <number or null>,
  "payment": {
    "method": "cash" | "credit_card" | "emoney" | "other" | null,
    "amount_paid": <number or null>,
    "change": <number or null>
  }
}

Rules:
- Use plain numbers (no currency symbols, no thousand separators, no "Rp"). E.g. 25000 not "25.000" or "Rp 25,000".
- If a field is not present in the image, use null. NEVER invent values.
- If a number is ambiguous or illegible, use null instead of guessing.
- Line items: every purchased item is one entry, in the order printed.
- unit_price: the price of ONE unit. If the receipt shows a line total (amount) for quantity > 1 but no explicit unit price, compute it: unit_price = amount / quantity (round to nearest integer). NEVER put the line total into unit_price.
- If quantity is 1, unit_price equals the line amount — use that value, not null.
- discount: use a positive number (e.g. 60000 not -60000), null if no discount is shown.
- payment.method: one of "cash", "credit_card", "emoney", "debit", "transfer", "other", or null. "QRIS"/"qris" counts as "emoney".
- payment.amount_paid: what the customer actually paid (or the charged card amount); null if not shown.
- payment.change: change returned to customer in cash; null if not shown or not applicable (e.g. card payment).
"""


def _read_9router_key():
    """Local dev: read active 9Router API key from its SQLite DB."""
    db_path = os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite")
    if not os.path.exists(db_path):
        return None
    db = sqlite3.connect(db_path)
    try:
        row = db.execute("SELECT key FROM apiKeys WHERE isActive = 1").fetchone()
        return row[0] if row else None
    finally:
        db.close()


def api_config():
    return {
        "base_url": os.environ.get("VLM_BASE_URL", "http://127.0.0.1:20128/v1"),
        "api_key": os.environ.get("VLM_API_KEY") or _read_9router_key(),
        "model": os.environ.get("VLM_MODEL", "bai/mimo-v2.5"),
        "timeout": int(os.environ.get("VLM_TIMEOUT_S", "180")),
        "max_tokens": int(os.environ.get("VLM_MAX_TOKENS", "3000")),
        "retries": int(os.environ.get("VLM_RETRIES", "3")),
    }


def image_to_data_url(image_bytes: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"


def call_vlm(image_bytes: bytes, prompt: str = SCHEMA_PROMPT, mime: str = "image/png", cfg=None):
    """Call VLM, return (raw_text, latency_s, ok). Handles 429/402/403 with backoff."""
    cfg = cfg or api_config()
    body = {
        "model": cfg["model"],
        "stream": False,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_to_data_url(image_bytes, mime)}},
            ],
        }],
        "max_tokens": cfg["max_tokens"],
    }
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
    )
    t0 = time.time()
    for attempt in range(cfg["retries"]):
        try:
            with urllib.request.urlopen(req, timeout=cfg["timeout"]) as resp:
                out = json.loads(resp.read().decode())
            return out["choices"][0]["message"]["content"] or "", time.time() - t0, True
        except urllib.error.HTTPError as e:
            err = e.read().decode()[:200]
            if e.code in (429, 402, 403):
                wait = 30 * (attempt + 1)
                time.sleep(wait)
            else:
                return "", time.time() - t0, False
        except Exception:
            if attempt < cfg["retries"] - 1:
                time.sleep(15)
    return "", time.time() - t0, False


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
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass
    return {}


def extract_receipt(image_bytes: bytes, mime: str = "image/png", cfg=None) -> dict:
    """Full pipeline: VLM -> parse JSON -> {prediction, latency_s, raw}."""
    cfg = cfg or api_config()
    raw, lat, ok = call_vlm(image_bytes, mime=mime, cfg=cfg)
    return {"prediction": parse_json_response(raw), "latency_s": round(lat, 2), "raw": raw, "ok": ok}
