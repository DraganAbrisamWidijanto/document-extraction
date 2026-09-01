"""Retry cord_059 alone with downscaling."""
import json, os, base64, time, sqlite3, urllib.request, urllib.error, io
from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
POOL = os.path.join(BASE, "data", "cord_pool")
OUT = os.path.join(BASE, "data", "predictions")

API_KEY = sqlite3.connect(os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite"))\
    .execute("SELECT key FROM apiKeys WHERE isActive = 1").fetchone()[0]

# inline prompt
PROMPT = """You are a document extraction system..."""  # simplified; just import from script
# actually import from retry_failed
import importlib.util as iu
spec = iu.spec_from_file_location("retry_failed", os.path.join(BASE, "retry_failed.py"))
mod = iu.module_from_spec(spec)
spec.loader.exec_module(mod)
PROMPT = mod.SCHEMA_PROMPT

def call(b64):
    body = {"model": "bai/mimo-v2.5", "stream": False,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
            ]}], "max_tokens": 4000}
    for att in range(3):
        try:
            req = urllib.request.Request("http://127.0.0.1:20128/v1/chat/completions",
                data=json.dumps(body).encode(),
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())["choices"][0]["message"]["content"] or ""
        except urllib.error.HTTPError as e:
            if e.code in (429,402,403): time.sleep(30*(att+1))
            else: return ""
        except Exception as e:
            print(f"  EXC: {e}")
            if att < 2: time.sleep(15)
    return ""

def parse(text):
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

img_path = os.path.join(POOL, "cord_059.png")
im = Image.open(img_path)
w, h = im.size
ratio = min(1600/w, 1600/h) if w>1600 or h>1600 else 1
if ratio < 1:
    im = im.resize((int(w*ratio), int(h*ratio)), Image.LANCZOS)
buf = io.BytesIO()
im.convert("RGB").save(buf, format="JPEG", quality=85)
print(f"img: {im.size[0]}x{im.size[1]} -> {buf.tell()//1024}KB")
b64 = base64.b64encode(buf.getvalue()).decode()
print("extracting...", flush=True)
t0 = time.time()
raw = call(b64)
dt = time.time() - t0
pred = parse(raw)
ok = bool(pred and pred.get("total") is not None)
print(f"{dt:.1f}s, {'OK' if ok else 'FAIL'}, total={pred.get('total') if pred else None}")
out = {"idx":"cord_059","model":"bai/mimo-v2.5","latency_s":round(dt,2),"raw":raw,"prediction":pred,"downscaled":True}
json.dump(out, open(os.path.join(OUT, "pred_cord_059.json"),"w"), indent=2)