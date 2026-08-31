import sqlite3, os, json, urllib.request

# Get 9Router API key from SQLite (avoids sk- masking)
DB = os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite")
db = sqlite3.connect(DB)
row = db.execute("SELECT key FROM apiKeys WHERE isActive = 1").fetchone()
api_key = row[0]
db.close()

# List models
req = urllib.request.Request(
    "http://127.0.0.1:20128/v1/models",
    headers={"Authorization": f"Bearer {api_key}"},
)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    ids = sorted(m["id"] for m in data.get("data", []))
    print(f"TOTAL MODELS: {len(ids)}")
    # Filter likely VLM models
    vlm_kw = ["minimax", "mimo", "gemini", "gpt-4o", "gpt-4.1", "vision", "vl", "qwen", "llava", "glm-4v", "kimi", "claude"]
    print("\n--- CANDIDATE VLM MODELS ---")
    for i in ids:
        low = i.lower()
        if any(k in low for k in vlm_kw):
            print(i)
except Exception as e:
    print("ERROR:", e)
