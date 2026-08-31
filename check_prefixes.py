import sqlite3, os, json

DB = os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite")
db = sqlite3.connect(DB)

print("=== providerNodes (all) ===")
for r in db.execute("SELECT id, type, name, data FROM providerNodes"):
    try:
        d = json.loads(r[3])
        prefix = d.get("prefix", "?")
    except Exception:
        prefix = "?"
    print(f"  {r[1]:25s} prefix={prefix:8s} name={r[2]}")

print("\n=== providerConnections (active) ===")
for r in db.execute("SELECT id, provider, name, isActive, data FROM providerConnections"):
    d = json.loads(r[4])
    print(f"  {r[2]:30s} active={r[3]} apiKey={str(d.get('apiKey',''))[:12]}...")

db.close()
