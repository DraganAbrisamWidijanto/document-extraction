"""Batch extraction for eval test set: 44 CORD (cord_pool) + 5 self-scan.

Usage:
  python batch_extract.py [--source cord|self|all] [--limit N]
Output: data/predictions/pred_<name>.json (raw + prediction + latency)
"""
import sqlite3, os, json, base64, sys, time, urllib.request, argparse

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "data", "predictions")
os.makedirs(OUT_DIR, exist_ok=True)

MODEL = "bai/mimo-v2.5"
MAX_TOKENS = 3000
RETRIES = 3
TIMEOUT = 180

DB = os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite")
db = sqlite3.connect(DB)
row = db.execute("SELECT key FROM apiKeys WHERE isActive = 1").fetchone()
API_KEY = row[0]
db.close()

# reuse the same prompt as dev_extract
import importlib.util
spec = importlib.util.spec_from_file_location("dev_extract", os.path.join(BASE, "dev_extract.py"))
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)
SCHEMA_PROMPT = dev.SCHEMA_PROMPT

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
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
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
    if not text:
        return {}
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
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end+1])
            except json.JSONDecodeError:
                pass
    return {}

def load_manifest(source):
    """Return list of (name, img_path, meta)."""
    items = []
    if source in ("cord", "all"):
        m = json.load(open(os.path.join(BASE, "data", "cord_pool", "manifest_test.json")))
        for e in m:
            img = e["image"]
            # images live either in cord_pool or dev_sample (idx 94)
            for d in ("data/cord_pool", "data/dev_sample"):
                p = os.path.join(BASE, d, img)
                if os.path.exists(p):
                    items.append((f"cord_{e['idx']:03d}", p, e))
                    break
    if source in ("self", "all"):
        m = json.load(open(os.path.join(BASE, "data", "self_scan", "manifest.json")))
        for e in m:
            p = os.path.join(BASE, "data", "self_scan", e["image"])
            items.append((e["idx"], p, e))
    return items

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="all", choices=["cord", "self", "all"])
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    items = load_manifest(args.source)
    if args.limit:
        items = items[:args.limit]
    print(f"Total to extract: {len(items)}")

    results = {}
    for name, img_path, meta in items:
        out_path = os.path.join(OUT_DIR, f"pred_{name}.json")
        if os.path.exists(out_path):
            print(f"[{name}] exists, skip")
            continue
        with open(img_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        print(f"[{name}] {os.path.basename(img_path)} extracting...", flush=True)
        t0 = time.time()
        raw = call_vlm(img_b64, SCHEMA_PROMPT)
        dt = time.time() - t0
        pred = parse_json_response(raw)
        print(f"    {dt:.1f}s, parsed={bool(pred)}, total={pred.get('total') if pred else None}", flush=True)
        out = {
            "name": name, "image": os.path.basename(img_path), "model": MODEL,
            "latency_s": round(dt, 2), "raw": raw, "prediction": pred,
        }
        with open(out_path, "w") as f:
            json.dump(out, f, indent=2)
        results[name] = out
        time.sleep(3)

    print("\n=== DONE ===")
    ok = sum(1 for r in results.values() if r["prediction"] and r["prediction"].get("total") is not None)
    print(f"ok={ok}/{len(results)}")

if __name__ == "__main__":
    main()
