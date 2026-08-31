import sqlite3, os, json, urllib.request, base64, struct, zlib, time, io

DB = os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite")
db = sqlite3.connect(DB)
row = db.execute("SELECT key FROM apiKeys WHERE isActive = 1").fetchone()
api_key = row[0]
db.close()

# Load a real CORD receipt image
image_path = r"C:\Users\ASUS\Documents\lamarankerja\datasaur\data\dev_sample\cord_003.png"
with open(image_path, "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode()

prompt = "Extract menu items and total from this receipt. Return JSON only."

# All mimo models from 9router
mimo_models = [
    "OM/mimo-v2-flash",
    "OM/mimo-v2-omni",
    "OM/mimo-v2-pro",
    "OM/mimo-v2.5",
    "OM/mimo-v2.5-pro",
    "agentic/mimo-v2-omni",
    "agentic/mimo-v2-pro",
    "agentic/mimo-v2.5",
    "agentic/mimo-v2.5-pro",
    "bai/mimo-v2.5",
    "bai/mimo-v2.5-pro",
    "deepseek_mimo",
    "gmi/XiaomiMiMo/MiMo-V2.5",
    "gmi/XiaomiMiMo/MiMo-V2.5-Pro",
    "ir/glw/xiaomi/mimo-v2.5",
    "ir/glw/xiaomi/mimo-v2.5-pro",
    "ir/mimo-v2.5",
    "ir/mimo-v2.5-pro",
    "ir/ocf/mimo-v2.5-free",
    "mimo/mimo-v2-flash",
    "mimo/mimo-v2-omni",
    "mimo/mimo-v2.5",
    "mimo/mimo-v2.5-pro",
    "oa/mimo-v2-omni",
    "oa/mimo-v2-pro",
    "oa/mimo-v2.5",
    "oa/mimo-v2.5-free",
    "oa/mimo-v2.5-pro",
    "oa/mimo-v2.5-pro-free",
    "wave/wr/mimo-v2.5-free",
    "ir/mf/mimo-auto",
]

def test_vlm(model):
    body = {
        "model": model,
        "stream": False,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ],
        }],
        "max_tokens": 500,
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
        msg = out["choices"][0]["message"]
        content = msg.get("content")
        finish = out["choices"][0]["finish_reason"]
        up_model = out.get("model", "")
        if content:
            status = "OK"
            snippet = content[:100].replace("\n", " ")
        else:
            reason = msg.get("reasoning_content", "")[:50]
            status = f"NO_CONTENT(reasoning:{reason}..)"
            snippet = ""
        print(f"[{status:30s}] {model:42s} ({time.time()-t0:.1f}s) → {up_model} | {snippet}")
    except urllib.error.HTTPError as e:
        body_err = e.read().decode()[:100].replace("\n", " ")
        print(f"[HTTP {e.code}            ] {model:42s} ({time.time()-t0:.1f}s) | {body_err}")
    except Exception as e:
        print(f"[EXC                 ] {model:42s} ({time.time()-t0:.1f}s) | {e}")
    time.sleep(1)

for m in mimo_models:
    test_vlm(m)