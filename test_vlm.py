import sqlite3, os, json, base64, time, urllib.request

DB = os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite")
db = sqlite3.connect(DB)
row = db.execute("SELECT key FROM apiKeys WHERE isActive = 1").fetchone()
api_key = row[0]
db.close()

# Make a tiny test image (1x1 red pixel PNG)
import struct, zlib
def make_png():
    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00\xff\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

png_b64 = base64.b64encode(make_png()).decode()

def test_vlm(model):
    body = {
        "model": model,
        "stream": False,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "What color is this image? One word."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_b64}"}},
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
        with urllib.request.urlopen(req, timeout=120) as resp:
            out = json.loads(resp.read().decode())
        msg = out["choices"][0]["message"]["content"]
        print(f"OK   {model} ({time.time()-t0:.1f}s): {msg[:80]}")
    except Exception as e:
        print(f"FAIL {model}: {e}")

for m in ["OM/mimo-v2.5", "OM/MiniMax-M3", "OM/deepseek-v4-flash-vision-exp", "OM/gemini-3.5-flash"]:
    test_vlm(m)
    time.sleep(2)
