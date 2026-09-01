"""compare_evals.py — A/B regression gate between two eval runs.

Compares two scorecards (results/scorecard_<split>.json) field-by-field and
flags any field whose F1 dropped more than --max-drop. Exit code 1 on regression
so CI / the compose eval step can gate a merge.

Usage:
  python compare_evals.py --base results/scorecard_cord_v1.json --new results/scorecard_cord_v2.json
  python compare_evals.py --base results/scorecard_cord.json --new results/scorecard_cord.json --max-drop 0.05
"""
import argparse
import json
import sys


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"error: {path} not found")
        sys.exit(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="baseline scorecard JSON")
    ap.add_argument("--new", required=True, help="new/candidate scorecard JSON")
    ap.add_argument("--max-drop", type=float, default=0.02,
                    help="max allowed F1 drop per field before regression (default 0.02)")
    ap.add_argument("--macro-max-drop", type=float, default=0.02,
                    help="max allowed macro F1 drop (default 0.02)")
    args = ap.parse_args()

    base, new = load(args.base), load(args.new)
    if base.get("split") != new.get("split"):
        print(f"warning: splits differ ({base.get('split')} vs {new.get('split')})")

    bf, nf = base.get("field_f1", {}), new.get("field_f1", {})
    all_fields = sorted(set(bf) | set(nf))
    print(f"{'field':<18} {'base F1':>7} {'new F1':>7} {'delta':>7}  status")
    print("-" * 52)
    regressions = []
    for f in all_fields:
        b = bf.get(f)
        n = nf.get(f)
        delta = None
        if b is not None and n is not None:
            delta = round(n - b, 3)
        status = "OK"
        if b is not None and n is not None and delta < -args.max_drop:
            status = f"REGRESSION (-{abs(delta):.3f} > {args.max_drop})"
            regressions.append((f, b, n, delta))
        print(f"{f:<18} {str(b):>7} {str(n):>7} {str(delta):>7}  {status}")

    bm, nm = base.get("macro_f1"), new.get("macro_f1")
    print("-" * 52)
    macro_ok = True
    if bm is not None and nm is not None:
        macro_delta = nm - bm
        macro_ok = macro_delta >= -args.macro_max_drop
        print(f"macro F1: {bm} -> {nm} (delta {macro_delta:+.3f}) "
              + ("OK" if macro_ok else f"REGRESSION (drop > {args.macro_max_drop})"))
    else:
        print(f"macro F1: base={bm} new={nm} (missing — cannot gate)")

    failed = bool(regressions) or not macro_ok
    if failed:
        print(f"\nFAIL: {len(regressions)} field regression(s)" + (", macro regression" if not macro_ok else ""))
        sys.exit(1)
    print("\nPASS: no regression beyond threshold")
    sys.exit(0)


if __name__ == "__main__":
    main()
