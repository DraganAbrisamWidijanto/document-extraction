"""Batch extraction for eval test set: 44 CORD (cord_pool) + 5 self-scan.

Usage:
  python batch_extract.py [--source cord|self|all] [--limit N] [--workers 4]
Output: data/predictions/pred_<name>.json (raw + prediction + usage + cost)
Concurrent: ThreadPoolExecutor with round-robin API key pool.

With --api-url, extraction goes through the FastAPI /extract endpoint instead
of the in-process pipeline (end-to-end eval against the running API):
  python batch_extract.py --source all --api-url http://localhost:8000
"""
import argparse
import json
import os
import urllib.request
import uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "data", "predictions")
os.makedirs(OUT_DIR, exist_ok=True)

from app.extractor import Config, extract_many


def load_manifest(source):
    """Return list of {name, path, mime}."""
    items = []
    if source in ("cord", "all"):
        m = json.load(open(os.path.join(BASE_DIR, "data", "cord_pool", "manifest_test.json")))
        for e in m:
            for d in ("data/cord_pool", "data/dev_sample"):
                p = os.path.join(BASE_DIR, d, e["image"])
                if os.path.exists(p):
                    items.append({"name": f"cord_{e['idx']:03d}", "path": p, "mime": "image/png"})
                    break
    if source in ("self", "all"):
        m = json.load(open(os.path.join(BASE_DIR, "data", "self_scan", "manifest.json")))
        for e in m:
            p = os.path.join(BASE_DIR, "data", "self_scan", e["image"])
            items.append({"name": e["idx"], "path": p, "mime": "image/jpeg"})
    return items


def extract_via_api(item, api_url, timeout_s=300):
    """POST one image to the FastAPI /extract endpoint."""
    with open(item["path"], "rb") as f:
        data = f.read()
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"image\"\r\nContent-Type: {item.get('mime', 'image/png')}\r\n\r\n"
            ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{api_url}/extract", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.load(resp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="all", choices=["cord", "self", "all"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--overwrite", action="store_true", help="re-extract existing preds")
    ap.add_argument("--api-url", default=None,
                    help="extract via HTTP API at this base URL (e.g. http://api:8000) "
                         "instead of the in-process pipeline")
    args = ap.parse_args()

    items = load_manifest(args.source)
    if args.limit:
        items = items[:args.limit]
    if not args.overwrite:
        todo, skipped = [], 0
        for it in items:
            out_path = os.path.join(OUT_DIR, f"pred_{it['name']}.json")
            if os.path.exists(out_path):
                skipped += 1
            else:
                todo.append(it)
        items = todo
        print(f"skip {skipped} existing, {len(items)} to extract")
    print(f"Total to extract: {len(items)}  (workers={args.workers}"
          + (f", via API {args.api_url}" if args.api_url else ", in-process") + ")")

    if args.api_url:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        cfg = Config.from_env()
        results = {}
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(extract_via_api, it, args.api_url): it["name"] for it in items}
            for fut in as_completed(futs):
                name = futs[fut]
                try:
                    res = fut.result()
                    results[name] = {
                        "name": name, "model": cfg.model,
                        "latency_s": res.get("latency_s"),
                        "raw": res.get("raw", ""), "prediction": res.get("prediction", {}),
                        "cost_usd": res.get("cost_usd"), "validation": res.get("validation"),
                    }
                except Exception as e:
                    results[name] = {"name": name, "ok": False, "error": str(e),
                                     "latency_s": 0, "prediction": {}, "raw": ""}
    else:
        cfg = Config.from_env()
        results = extract_many(items, cfg, workers=args.workers)

    for name, res in results.items():
        out_path = os.path.join(OUT_DIR, f"pred_{name}.json")
        record = {
            "name": name, "image": os.path.basename(res.get("path", "")), "model": cfg.model,
            "latency_s": res.get("latency_s"), "raw": res.get("raw", ""),
            "prediction": res.get("prediction", {}), "usage": res.get("usage"),
            "cost_usd": res.get("cost_usd"), "downscaled": res.get("downscaled", False),
            "validation": res.get("validation"),
        }
        with open(out_path, "w") as f:
            json.dump(record, f, indent=2)

    ok = sum(1 for r in results.values()
             if r.get("prediction") and r["prediction"].get("total") is not None)
    total_cost = sum(r.get("cost_usd", 0) for r in results.values())
    print(f"\n=== DONE === ok={ok}/{len(results)}  cost=${total_cost:.6f}")


if __name__ == "__main__":
    main()