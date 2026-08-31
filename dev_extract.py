"""Dev loop: extract structured fields from receipt images using VLM via 9Router.

Usage:
  python dev_extract.py [idx1 idx2 ...]
  (default: the 6 selected diverse samples)
"""
import sqlite3, os, json, base64, sys, time, urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(BASE, "data", "dev_sample")
OUT_DIR = os.path.join(BASE, "data", "dev_predictions")
os.makedirs(OUT_DIR, exist_ok=True)

MODEL = "bai/mimo-v2.5"
MAX_TOKENS = 3000
RETRIES = 3

DB = os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite")
db = sqlite3.connect(DB)
row = db.execute("SELECT key FROM apiKeys WHERE isActive = 1").fetchone()
API_KEY = row[0]
db.close()

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

def call_vlm(image_b64: str, prompt: str) -> str:
    body = {
        "model": MODEL,
        "stream": False,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }],
        "max_tokens": MAX_TOKENS,
    }
    req = urllib.request.Request(
        "http://127.0.0.1:20128/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    )
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                out = json.loads(resp.read().decode())
            return out["choices"][0]["message"]["content"] or ""
        except urllib.error.HTTPError as e:
            err = e.read().decode()[:200]
            print(f"    HTTP {e.code}: {err}")
            if e.code in (429, 402, 403):
                wait = 30 * (attempt + 1)
                print(f"    retrying in {wait}s...")
                time.sleep(wait)
            else:
                return ""
        except Exception as e:
            print(f"    EXC: {e}")
            if attempt < RETRIES - 1:
                time.sleep(15)
    return ""

def parse_json_response(text: str) -> dict:
    """Extract JSON from model response (handles markdown fences)."""
    if not text:
        return {}
    # Strip markdown fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find first { ... } block
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end+1])
            except json.JSONDecodeError:
                pass
    return {}

def main():
    manifest = json.load(open(os.path.join(SAMPLE_DIR, "manifest.json")))
    by_idx = {m["idx"]: m for m in manifest}

    if len(sys.argv) > 1:
        idxs = [int(a) for a in sys.argv[1:]]
    else:
        # 6 diverse samples: high-res photo, low-res, multi-item+tax, CC, discount, tiny
        idxs = [14, 94, 28, 0, 75, 31]

    results = {}
    for idx in idxs:
        m = by_idx[idx]
        img_path = os.path.join(SAMPLE_DIR, m["image"])
        with open(img_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        print(f"[{idx}] {m['image']} ({m['size'][0]}x{m['size'][1]}) extracting...")
        t0 = time.time()
        raw = call_vlm(img_b64, SCHEMA_PROMPT)
        dt = time.time() - t0
        pred = parse_json_response(raw)
        print(f"    {dt:.1f}s, raw_len={len(raw)}, parsed={bool(pred)}")
        if pred:
            print(f"    total={pred.get('total')}, items={len(pred.get('menu_items') or [])}")
        else:
            print(f"    RAW: {raw[:300]}")
        # Save
        out = {
            "idx": idx, "image": m["image"], "model": MODEL,
            "latency_s": round(dt, 2), "raw": raw, "prediction": pred,
        }
        with open(os.path.join(OUT_DIR, f"pred_{idx:03d}.json"), "w") as f:
            json.dump(out, f, indent=2)
        results[idx] = out
        time.sleep(3)  # be gentle with rate limits

    print("\n=== DONE ===")
    for idx, r in results.items():
        p = r["prediction"]
        ok = "✓" if p and p.get("total") is not None else "✗"
        print(f"  {ok} idx={idx}: total={p.get('total') if p else None}, items={len(p.get('menu_items') or []) if p else 0}, {r['latency_s']}s")

if __name__ == "__main__":
    main()
