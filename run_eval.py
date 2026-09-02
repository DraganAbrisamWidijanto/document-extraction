"""run_eval.py — end-to-end eval: extract fresh via the pipeline, then score.

Default: reuse existing predictions in data/predictions (fast, no API cost).
With --live: re-extract every doc through app.extractor (the same code path the
FastAPI /extract endpoint uses) then run the harness — this is the
"docker compose run --rm eval" end-to-end flow.

Usage:
  python run_eval.py                 # score existing predictions
  python run_eval.py --live          # extract fresh, then score (API calls!)
  python run_eval.py --split cord    # only the CORD split
  python run_eval.py --workers 4     # concurrent extraction (live mode)
"""
import argparse
import json
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULT_DIR = os.path.join(BASE_DIR, "results")
os.makedirs(RESULT_DIR, exist_ok=True)

PY = sys.executable

SPLITS = {
    "cord": {
        "manifest": "data/cord_pool/manifest_test.json",
        "gt_format": "cord",
        "source": "cord",
        "pred_glob": "data/cord_pool/manifest_test.json",
    },
    "self": {
        "manifest": "data/self_scan/manifest.json",
        "gt_format": "self",
        "source": "self",
        "pred_glob": "data/self_scan/manifest.json",
    },
    "kleister": {
        "manifest": "data/kleister/manifest.json",
        "gt_format": "kleister",
        "source": "kleister",
        "pred_glob": "data/kleister/manifest.json",
    },
}


def run_eval(name):
    """Score one split via eval_harness.py."""
    split = SPLITS[name]
    args = ["--manifest", split["manifest"], "--pred-dir", "data/predictions",
            "--gt-format", split["gt_format"]]
    print(f"\n########## {name.upper()} ##########")
    r = subprocess.run([PY, "eval_harness.py"] + args, capture_output=True, text=True, cwd=BASE_DIR)
    print(r.stdout)
    if r.returncode != 0:
        print("STDERR:", r.stderr[-2000:])
    return r.returncode


def extract_live(splits, workers, api_url=None):
    """Re-extract via batch_extract.py so eval runs against fresh API output.

    With api_url set, extraction goes through the running FastAPI /extract
    endpoint (end-to-end against the API, per the take-home spec); otherwise
    it uses the in-process pipeline (same code path, no HTTP hop).
    """
    src = "all" if set(splits) == {"cord", "self", "kleister"} else splits[0]
    cmd = [PY, "batch_extract.py", "--source", src, "--workers", str(workers), "--overwrite"]
    if api_url:
        cmd += ["--api-url", api_url]
    print(f"\n>>> LIVE EXTRACTION: {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=BASE_DIR)
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="re-extract before scoring")
    ap.add_argument("--split", default="all", choices=["all", "cord", "self", "kleister"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--api-url", default=None,
                    help="with --live: extract via the API at this base URL "
                         "(e.g. http://localhost:8000) instead of in-process")
    args = ap.parse_args()

    splits = ["cord", "self", "kleister"] if args.split == "all" else [args.split]

    if args.live:
        rc = extract_live(splits, args.workers, args.api_url)
        if rc != 0:
            sys.exit(rc)

    rc = 0
    for name in splits:
        rc |= run_eval(name)
    sys.exit(rc)


if __name__ == "__main__":
    main()
