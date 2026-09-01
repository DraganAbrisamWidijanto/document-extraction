"""Retry failed CORD images with image downscaling (max 1600px) to avoid timeouts.
Usage: python retry_failed.py [--image cord_059, cord_070, ...]  (default: all 7)
"""
import sys, os, base64, time, json, urllib.request, urllib.error, sqlite3, io
from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "data", "predictions")
POOL_DIR = os.path.join(BASE, "data", "cord_pool")

MODEL = "bai/mimo-v2.5"
RETRIES = 3
TIMEOUT = 120

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
- Use plain numbers (no separators, no "Rp"). E.g. 25000 not "25.000".
- null if not present. NEVER invent.
- unit_price = amount / quantity when qty>1 and no explicit unit price.
- If qty=1, unit_price = amount (never null).
- discount: positive number, null if no discount.
- payment.method: "cash"|"credit_card"|"emoney"|"debit"|"transfer"|null.
"""


def _api_key():
    db = sqlite3.connect(os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite"))
    try: return db.execute("SELECT key FROM apiKeys WHERE isActive = 1").fetchone()[0]
    finally: db.close()


def downscale(img_path, max_dim=1600):
    im = Image.open(img_path)
    w, h = im.size
    if w > max_dim or h > max_dim:
        ratio = min(max_dim / w, max_dim / h)
        im = im.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=85)
    orig = os.path.getsize(img_path)
    print(f"  {os.path.basename(img_path)}: {w}x{h} {orig//1024}KB -> {buf.tell()//1024}KB ({buf.tell()*100//orig}%)")
    return buf.getvalue()


def call_vlm(image_b64, api_key, timeout=TIMEOUT, max_tokens=4000):
    body = {"model": MODEL, "stream": False,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": SCHEMA_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ]}], "max_tokens": max_tokens}
    for att in range(RETRIES):
        try:
            req = urllib.request.Request("http://127.0.0.1:20128/v1/chat/completions",
                data=json.dumps(body).encode(),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())["choices"][0]["message"]["content"] or ""
        except urllib.error.HTTPError as e:
            err = e.read().decode()[:200]
            print(f"    HTTP {e.code}: {err}")
            if e.code in (429, 402, 403): time.sleep(30 * (att + 1))
            else: return ""
        except Exception as e:
            print(f"    EXC: {e}")
            if att < RETRIES - 1: time.sleep(15)
    return ""


def parse_json(text):
    if not text: return {}
    cl = text.strip()
    if cl.startswith("```"):
        cl = "\n".join(cl.split("\n")[1:])
        if cl.endswith("```"): cl = cl[:-3].strip()
    try: return json.loads(cl)
    except:
        s, e = cl.find("{"), cl.rfind("}")
        if s != -1 and e > s:
            try: return json.loads(cl[s:e+1])
            except: pass
    return {}


if __name__ == "__main__":
    # which images to retry
    targets = sys.argv[1:] if len(sys.argv) > 1 else [
        "cord_043", "cord_059", "cord_070", "cord_074",
        "cord_081", "cord_098", "cord_099"
    ]
    api_key = _api_key()
    results = {}

    for name in targets:
        img_path = os.path.join(POOL_DIR, f"{name}.png")
        if not os.path.exists(img_path):
            print(f"[{name}] not found, skip")
            continue
        print(f"[{name}] downscale + extract...", flush=True)
        jpeg = downscale(img_path)
        t0 = time.time()
        raw = call_vlm(base64.b64encode(jpeg).decode(), api_key)
        dt = time.time() - t0
        pred = parse_json(raw)
        ok = bool(pred and pred.get("total") is not None)
        print(f"  {dt:.1f}s, {'OK' if ok else 'FAIL'}, total={pred.get('total') if pred else None}")
        json.dump({"idx": name, "model": MODEL, "latency_s": round(dt, 2),
                    "raw": raw, "prediction": pred, "downscaled": True},
                  open(os.path.join(OUT_DIR, f"pred_{name}.json"), "w"), indent=2)
        results[name] = ok
        time.sleep(3)

    print(f"\n=== RESULTS ===")
    for name, ok in results.items():
        print(f"  {'✓' if ok else '✗'} {name}")
    print(f"  total: {sum(results.values())}/{len(results)}")