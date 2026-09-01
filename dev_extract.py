"""Dev loop: extract a batch of receipts using the shared pipeline.

Usage: python dev_extract.py [idx1 idx2 ...]
  (default: the 6 diverse dev samples)
"""
import json
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(BASE_DIR, "data", "dev_sample")
OUT_DIR = os.path.join(BASE_DIR, "data", "dev_predictions")
os.makedirs(OUT_DIR, exist_ok=True)

from app.extractor import Config, extract_receipt, parse_json_response


def main():
    manifest = json.load(open(os.path.join(SAMPLE_DIR, "manifest.json")))
    by_idx = {m["idx"]: m for m in manifest}

    idxs = [int(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else [14, 94, 28, 0, 75, 31]

    cfg = Config.from_env()
    print(f"Model: {cfg.model}  Keys: {len(cfg.keys)}  Price: ${cfg.price_input_per_m}/M in")

    for idx in idxs:
        meta = by_idx[idx]
        img_path = os.path.join(SAMPLE_DIR, meta["image"])
        with open(img_path, "rb") as f:
            img_bytes = f.read()

        print(f"[{idx}] {meta['image']} ({meta['size'][0]}x{meta['size'][1]}) extracting...")
        result = extract_receipt(img_bytes, "image/png", cfg)
        pred = result["prediction"]
        print(f"    {result['latency_s']:.1f}s ok={result['ok']} "
              f"total={pred.get('total') if pred else None} "
              f"items={len(pred.get('menu_items') or []) if pred else 0} "
              f"cost=${result['cost_usd']:.6f} "
              f"valid={result['validation']['valid']}")

        out = {
            "idx": idx, "image": meta["image"], "model": cfg.model,
            "latency_s": result["latency_s"], "raw": result["raw"],
            "prediction": pred, "usage": result["usage"],
            "cost_usd": result["cost_usd"], "downscaled": result["downscaled"],
            "validation": result["validation"],
        }
        with open(os.path.join(OUT_DIR, f"pred_{idx:03d}.json"), "w") as f:
            json.dump(out, f, indent=2)
        time.sleep(1)


if __name__ == "__main__":
    main()