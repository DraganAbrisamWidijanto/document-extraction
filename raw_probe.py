import sqlite3, os, json, urllib.request, time, base64, struct, zlib

DB = os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite")
db = sqlite3.connect(DB)
row = db.execute("SELECT key FROM apiKeys WHERE isActive = 1").fetchone()
api_key = row[0]
db.close()

def make_png():
    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00\xff\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

png_b64 = base64.b64encode(make_png()).decode()

def raw_test(model, with_image=False):
    content = [
        {"type": "text", "text": "What color is this image? One word."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_b64}"}},
    ] if with_image else "Say ok"
    body = {
        "model": model,
        "stream": False,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 30,
    }
    req = urllib.request.Request(
        "http://127.0.0.1:20128/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            print(f"[{model} img={with_image}] HTTP {resp.status}")
            print("  RAW:", raw[:600])
    except urllib.error.HTTPError as e:
        print(f"[{model} img={with_image}] HTTPError {e.code}")
        print("  BODY:", e.read().decode()[:600])
    except Exception as e:
        print(f"[{model} img={with_image}] EXC {e}")
    print()

for m in ["oa/mimo-v2.5-free", "ir/ocf/mimo-v2.5-free", "wave/wr/mimo-v2.5-free",
          "bai/minimax-m3", "agentic/minimax-m3", "bai/deepseek-v4-flash-vision-exp"]:
    raw_test(m, with_image=True)
    time.sleep(1)
