import sqlite3, os, json, urllib.request, base64, struct, zlib, time

DB = os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite")
db = sqlite3.connect(DB)
row = db.execute("SELECT key FROM apiKeys WHERE isActive = 1").fetchone()
api_key = row[0]
db.close()

# List all models, filter by our prefixes
req = urllib.request.Request(
    "http://127.0.0.1:20128/v1/models",
    headers={"Authorization": f"Bearer {api_key}"},
)
with urllib.request.urlopen(req, timeout=10) as resp:
    data = json.loads(resp.read().decode())
ids = [m["id"] for m in data.get("data", [])]

# Search for the user's models
searches = ["oc/mimo", "bai/minimax", "bai/deepseek", "oc/", "bai/"]
for s in searches:
    matches = [i for i in ids if s.lower() in i.lower()]
    if matches:
        print(f"--- {s} ---")
        for m in sorted(matches)[:10]:
            print(f"  {m}")

# Also test if they exist
print("\n\nTesting specific models...")
def test_vlm(model, image_b64=None):
    if image_b64 is None:
        # Test without image first (text-only)
        body = {
            "model": model,
            "stream": False,
            "messages": [{"role": "user", "content": "Say 'ok' in one word."}],
            "max_tokens": 10,
        }
    else:
        body = {
            "model": model,
            "stream": False,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "What color? One word."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                ],
            }],
            "max_tokens": 50,
        }
    req = urllib.request.Request(
        "http://127.0.0.1:20128/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            out = json.loads(resp.read().decode())
        msg = out["choices"][0]["message"]["content"]
        print(f"OK   {model} ({time.time()-t0:.1f}s): {msg[:80]}")
        return True
    except Exception as e:
        print(f"FAIL {model}: {e}")
        return False

# Make test image
def make_png():
    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00\xff\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
png_b64 = base64.b64encode(make_png()).decode()

for m in ["oc/mimo-v2.5-free", "bai/minimax-m3", "bai/deepseek-v4-flash-vision-exp"]:
    # Text-only first
    test_vlm(m)
    time.sleep(1)
    # Then with image
    test_vlm(m, png_b64)
    time.sleep(2)