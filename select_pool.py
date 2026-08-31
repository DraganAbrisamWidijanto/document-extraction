"""Select 50 diverse CORD images from 100, assign dev(5)+test(45).
Usage: python select_pool.py
Output: data/cord_pool/manifest.json + extract PNGs"""
import pandas as pd, json, os, base64
from PIL import Image
import io
import heapq

BASE = r"C:\Users\ASUS\Documents\lamarankerja\datasaur"
PARQUET = os.path.join(BASE, "data", "cord_test.parquet")
POOL_DIR = os.path.join(BASE, "data", "cord_pool")
os.makedirs(POOL_DIR, exist_ok=True)

df = pd.read_parquet(PARQUET)
print(f"Total rows: {len(df)}")

def to_num(s):
    """Parse CORD price strings: 'Rp 111000', '-60.000', '24,000', '0'. Returns float or None."""
    if s is None: return None
    t = str(s).strip().replace("Rp", "").replace("rp", "").replace(" ", "")
    t = t.replace(",", "").replace(".", "")
    if t == "" or t == "-" or t in ("0", "-0"):
        return 0.0
    try:
        return float(t)
    except ValueError:
        return None

# Extract features per row
rows = []
for i in range(len(df)):
    gt = json.loads(df.iloc[i]["ground_truth"])
    gp = gt["gt_parse"]
    meta = gt["meta"]
    img_id = meta["image_id"]
    w, h = meta["image_size"]["width"], meta["image_size"]["height"]
    
    menu = gp.get("menu", [])
    if isinstance(menu, dict): menu = [menu]
    n_items = len(menu)
    
    sub = gp.get("sub_total", {})
    tot = gp.get("total", {})
    has_tax = int(to_num(sub.get("tax_price")) not in (None, 0.0))
    has_discount = int(to_num(sub.get("discount_price")) not in (None, 0.0))
    has_service = int(to_num(sub.get("service_price")) not in (None, 0.0))
    
    # payment type: cash/cc/emoney/gift/none
    pay = 4  # none
    for pv, pname in [(tot.get("cashprice"), 0), (tot.get("creditcardprice"), 1), 
                        (tot.get("emoneyprice"), 2), (tot.get("giftprice"), 3)]:
        v = to_num(pv)
        if v not in (None, 0.0):
            pay = pname
            break
    
    # area category (log scale)
    area = w * h
    a_cat = 0 if area > 2_000_000 else (1 if area > 500_000 else 2)
    
    rows.append({
        "image_id": img_id, "n_items": n_items,
        "has_tax": has_tax, "has_discount": has_discount, "has_service": has_service,
        "pay_type": pay, "area_cat": a_cat, "area": area,
        "row_idx": i, "gt": gt, "img_bytes": df.iloc[i]["image"]["bytes"],
    })

# Known tested indices (6 already extracted)
TESTED = {0, 14, 28, 31, 75, 94}  # these have pred_XXX.json
# We want: dev_idx=[0,14,28,31,75], harness_idx=[94] + 44 new

import random
random.seed(42)

def feature_vec(r):
    """Richer categorical feature vector for diversity."""
    return (min(r["n_items"], 8),          # 0..8 granular
            r["has_tax"], r["has_discount"], r["has_service"],
            r["pay_type"], r["area_cat"])

def hamming(v1, v2):
    return sum(a != b for a, b in zip(v1, v2))

# Separate: tested (6), candidate (94)
tested_rows = [r for r in rows if r["image_id"] in TESTED]
candidates = [r for r in rows if r["image_id"] not in TESTED]
random.shuffle(candidates)  # randomize tie-breaking

def min_dist(item, selected_set):
    fv = feature_vec(item)
    return min((hamming(fv, feature_vec(s)) for s in selected_set), default=99)

# Seed: candidate farthest from any tested row (ties broken randomly by shuffle)
selected = []
dists = [(min_dist(r, tested_rows), r["area"], r) for r in candidates]
best = max(dists, key=lambda t: (t[0], t[1]))
candidates.remove(best[2])
selected.append(best[2])
selected_set = tested_rows + [best[2]]

for _ in range(43):
    if not candidates:
        break
    dists = [(min_dist(r, selected_set), r["area"], r) for r in candidates]
    picked = max(dists, key=lambda t: (t[0], t[1]))
    candidates.remove(picked[2])
    selected.append(picked[2])
    selected_set.append(picked[2])

all_selected = selected  # 44 new picks

print(f"\nSelected {len(all_selected)} new images + 6 tested = {len(all_selected)+6} total")
print(f"Already tested ids (6): {sorted(r['image_id'] for r in tested_rows)}")
print(f"New ids ({len(all_selected)}): {sorted(r['image_id'] for r in all_selected)}")

# Build manifest
# Dev = [0,14,28,31,75], Harness = [94] + all new
DEV_IDS = {0, 14, 28, 31, 75}
HARNESS_IDS = {94} | {r["image_id"] for r in all_selected}

def get_row_by_id(id_, src):
    for r in src:
        if r["image_id"] == id_:
            return r
    return None

# Write images + manifest
manifest_dev = []
manifest_harness = []

for id_ in sorted(DEV_IDS):
    r = get_row_by_id(id_, tested_rows)
    # Already has image in dev_sample — reference it
    manifest_dev.append({
        "idx": id_, "image": f"cord_{id_:03d}.png",  # in dev_sample
        "gt_type": "cord", "split": "dev",
    })

for id_ in sorted(HARNESS_IDS):
    if id_ in TESTED:
        r = get_row_by_id(id_, tested_rows)
        img_path = f"cord_{id_:03d}.png"  # dev_sample
    else:
        r = get_row_by_id(id_, all_selected)
        if r is None:
            print(f"  WARNING: id {id_} not found in any pool")
            continue
        # Write PNG
        img_bytes = r["img_bytes"]
        if isinstance(img_bytes, dict):
            img_bytes = img_bytes["bytes"]
        fname = f"cord_{id_:03d}.png"
        img_path = fname
        with open(os.path.join(POOL_DIR, fname), "wb") as f:
            f.write(img_bytes if isinstance(img_bytes, bytes) else bytes(img_bytes))
    
    manifest_harness.append({
        "idx": id_, "image": img_path, "gt_type": "cord", "split": "test",
    })

# Save manifests
with open(os.path.join(POOL_DIR, "manifest_dev.json"), "w") as f:
    json.dump(manifest_dev, f, indent=2)
with open(os.path.join(POOL_DIR, "manifest_test.json"), "w") as f:
    json.dump(manifest_harness, f, indent=2)

print(f"\nDev: {len(manifest_dev)} images")
for m in manifest_dev:
    print(f"  {m['idx']:03d} -> {m['image']}")
print(f"Test harness (CORD): {len(manifest_harness)} images")
print(f"Test harness (self-scan): 5")
print(f"Total eval harness: {len(manifest_harness) + 5}")