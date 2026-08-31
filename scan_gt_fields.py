import json, pandas as pd
from collections import Counter

df = pd.read_parquet(r"C:\Users\ASUS\datasaur_takehome\data\cord_test.parquet")

top_keys = Counter()
sub_keys = Counter()
all_fields = set()
multi_menu = 0
for gt_raw in df["ground_truth"]:
    gt = json.loads(gt_raw)
    parse = gt.get("gt_parse", {})
    for k, v in parse.items():
        top_keys[k] += 1
        if isinstance(v, dict):
            for kk in v:
                sub_keys[f"{k}.{kk}"] += 1
                all_fields.add(f"{k}.{kk}")
    if isinstance(parse.get("menu"), list):
        multi_menu += 1

print("TOP-LEVEL keys:", dict(top_keys))
print()
print("SUB keys present (count>0):")
for k in sorted(all_fields):
    print(f"  {k}: {sub_keys[k]}")
print()
print("Samples with multi-item menu (list):", multi_menu, "/", len(df))
