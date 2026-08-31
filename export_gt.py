"""Export GT JSON for cord_pool selected images from parquet (gt_parse format)."""
import pandas as pd, json, os

BASE = r"C:\Users\ASUS\Documents\lamarankerja\datasaur"
PARQUET = os.path.join(BASE, "data", "cord_test.parquet")
POOL = os.path.join(BASE, "data", "cord_pool")

df = pd.read_parquet(PARQUET)

# id -> gt_parse
by_id = {}
for i in range(len(df)):
    gt = json.loads(df.iloc[i]["ground_truth"])
    by_id[gt["meta"]["image_id"]] = gt

# export GT for all ids in manifests (dev has GT in dev_sample already, but export for test)
for mfile in ("manifest_test.json", "manifest_dev.json"):
    m = json.load(open(os.path.join(POOL, mfile)))
    for e in m:
        idx = e["idx"]
        gt = by_id.get(idx)
        if gt is None:
            print(f"  WARN no GT for {idx}")
            continue
        out = os.path.join(POOL, f"cord_{idx:03d}.json")
        with open(out, "w") as f:
            json.dump(gt, f, indent=2)
    print(f"exported GT for {len(m)} {mfile.split('_')[1].split('.')[0]} images")

# also self_scan GT already written; verify count
import glob
print("cord_pool GT files:", len(glob.glob(os.path.join(POOL, "cord_*.json"))))
