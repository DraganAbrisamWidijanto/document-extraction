import pandas as pd
df = pd.read_parquet(r"C:\Users\ASUS\datasaur_takehome\data\cord_test.parquet")
print("SHAPE:", df.shape)
print("COLS:", list(df.columns))
for c in df.columns:
    print(f"\n--- {c} dtype={df[c].dtype} ---")
    sample = df[c].iloc[0]
    if hasattr(sample, "__len__") and not isinstance(sample, (str, bytes)):
        print("  len:", len(sample))
        if isinstance(sample, (dict, list)):
            print("  sample:", str(sample)[:500])
    else:
        print("  sample:", str(sample)[:500])
