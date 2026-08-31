import json, os, random, io
import pandas as pd
from PIL import Image

BASE = r"C:\Users\ASUS\Documents\lamarankerja\datasaur"
OUT = os.path.join(BASE, "data", "dev_sample")
os.makedirs(OUT, exist_ok=True)

df = pd.read_parquet(os.path.join(BASE, "data", "cord_test.parquet"))
N = len(df)
print("Total rows:", N)

# Random sample with fixed seed for reproducibility
random.seed(42)
n_sample = 30
idxs = random.sample(range(N), n_sample)

manifest = []
for i, idx in enumerate(idxs):
    img_bytes = df["image"].iloc[idx]["bytes"]
    gt = json.loads(df["ground_truth"].iloc[idx])
    # Save image
    fname = f"cord_{idx:03d}.png"
    with open(os.path.join(OUT, fname), "wb") as f:
        f.write(img_bytes)
    # Save GT
    gtname = f"cord_{idx:03d}.json"
    with open(os.path.join(OUT, gtname), "w") as f:
        json.dump(gt, f, indent=2)
    # Record image dims
    im = Image.open(io.BytesIO(img_bytes))
    manifest.append({
        "idx": idx,
        "image": fname,
        "gt": gtname,
        "size": list(im.size),
        "fields": list(gt.get("gt_parse", {}).keys()),
    })

with open(os.path.join(OUT, "manifest.json"), "w") as f:
    json.dump(manifest, f, indent=2)

print(f"Sampled {n_sample} images to {OUT}")
for m in manifest[:5]:
    print(m)
