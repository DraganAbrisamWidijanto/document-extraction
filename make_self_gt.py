"""Generate draft GT for self_006..010 using VLM.
Strips confidence fields from prediction to match GT format.
"""
import json, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
SELF_DIR = os.path.join(BASE, "data", "self_scan")

from app.extractor import Config, extract_receipt


def strip_confidence(obj):
    """Recursively remove 'confidence' keys from nested dicts."""
    if isinstance(obj, dict):
        return {k: strip_confidence(v) for k, v in obj.items() if k != "confidence"}
    elif isinstance(obj, list):
        return [strip_confidence(i) for i in obj]
    return obj


def main():
    cfg = Config.from_env()
    cfg.retries = 2  # less retry for draft GT

    images = [f"self_{i:03d}" for i in range(6, 11)]
    for name in images:
        img_path = os.path.join(SELF_DIR, f"{name}.jpg")
        if not os.path.exists(img_path):
            print(f"[SKIP] {img_path} not found")
            continue

        with open(img_path, "rb") as f:
            data = f.read()

        print(f"[{name}] extracting...", flush=True)
        res = extract_receipt(data, "image/jpeg", cfg)
        pred = res.get("prediction", {})

        # strip confidence
        clean_pred = strip_confidence(pred)

        # build GT
        gt = {
            "prediction": clean_pred,
            "meta": {
                "vendor": None,
                "date": None,
                "source": "photo",
                "note": "VLM-generated draft GT — review & verify before eval",
            },
        }

        out_path = os.path.join(SELF_DIR, f"{name}.json")
        with open(out_path, "w") as f:
            json.dump(gt, f, indent=2, ensure_ascii=False)

        print(f"    {res['latency_s']:.1f}s ok={res['ok']} "
              f"total={pred.get('total') if pred else None} "
              f"items={len(pred.get('menu_items') or []) if pred else 0}"
              f" -> {out_path}", flush=True)

    print("\n=== DONE ===")


if __name__ == "__main__":
    main()