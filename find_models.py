import sqlite3, os, json, urllib.request, time

DB = os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite")
db = sqlite3.connect(DB)
row = db.execute("SELECT key FROM apiKeys WHERE isActive = 1").fetchone()
api_key = row[0]
db.close()

# List ALL models, find free variants and oc prefix
req = urllib.request.Request(
    "http://127.0.0.1:20128/v1/models",
    headers={"Authorization": f"Bearer {api_key}"},
)
with urllib.request.urlopen(req, timeout=10) as resp:
    data = json.loads(resp.read().decode())
ids = [m["id"] for m in data.get("data", [])]

print("=== models containing 'free' ===")
for i in sorted(ids):
    if "free" in i.lower():
        print(" ", i)

print("\n=== all 'oc/' prefix ===")
for i in sorted(ids):
    if i.lower().startswith("oc/"):
        print(" ", i)

print("\n=== all mimo models ===")
for i in sorted(ids):
    if "mimo" in i.lower():
        print(" ", i)

print("\n=== all minimax ===")
for i in sorted(ids):
    if "minimax" in i.lower():
        print(" ", i)
