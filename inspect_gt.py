import json, pandas as pd

df = pd.read_parquet(r"C:\Users\ASUS\datasaur_takehome\data\cord_test.parquet")

# Show 3 samples of GT structure
for i in range(3):
    gt = json.loads(df["ground_truth"].iloc[i])
    print(f"=== Sample {i} ===")
    print("gt_parse keys:", list(gt.get("gt_parse", {}).keys()))
    # Print the full gt_parse
    print(json.dumps(gt.get("gt_parse", {}), indent=2)[:600])
    print()