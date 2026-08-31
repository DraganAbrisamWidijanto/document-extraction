import sqlite3, os, json, urllib.request, base64, struct, zlib, time

DB = os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite")
db = sqlite3.connect(DB)
row = db.execute("SELECT key FROM apiKeys WHERE isActive = 1").fetchone()
api_key = row[0]
db.close()

# Load a real CORD image
import io
image_path = r"C:\Users\ASUS\Documents\lamarankerja\datasaur\data\dev_sample\cord_003.png"
with open(image_path, "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode()

def test_extraction(model, prompt, max_tokens=500):
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
        "max_tokens": max_tokens,
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
        reasoning = msg.get("reasoning_content", "")
        print(f"OK   {model} ({time.time()-t0:.1f}s)")
        if content:
            print(f"  CONTENT: {content[:300]}")
        elif reasoning:
            print(f"  REASONING (no content yet): {reasoning[:200]}...")
        else:
            print("  No content or reasoning!")
        print(f"  finish_reason: {out['choices'][0]['finish_reason']}")
        print(f"  model_returned: {out['model']}")
        return content
    except Exception as e:
        print(f"FAIL {model}: {e}")
        return None

# Test with higher max_tokens
prompt = "Extract all fields from this receipt. Return JSON with: menu items (name, quantity, price), subtotal, tax, total. Use Indonesian rupiah format."
test_extraction("bai/deepseek-v4-flash-vision-exp", prompt, max_tokens=2000)