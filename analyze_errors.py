"""Deep error analysis: categorize where predictions go wrong (CORD + SELF).

Classifies errors into fixable classes vs pure model issues.
Output: per-doc error detail + aggregate by type.
"""
import json, os, re
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
PRED = os.path.join(BASE, "data", "predictions")
SELF_DIR = os.path.join(BASE, "data", "self_scan")
CORD_DIR = os.path.join(BASE, "data", "cord_pool")

import sys
sys.path.insert(0, BASE)
from eval_harness import (to_num, name_norm, gt_cord_to_schema, gt_self_to_schema,
                          compare_scalar, compare_items, SCALAR_FIELDS, PAY_FIELDS, ITEM_FIELDS)


def analyze_split(split, manifest_path, gt_format, out_rows, agg):
    man = json.load(open(os.path.join(BASE, manifest_path)))
    gdir = SELF_DIR if split == "self" else CORD_DIR
    for e in man:
        stem = os.path.splitext(e["image"])[0]
        gt_raw = json.load(open(os.path.join(gdir, f"{stem}.json")))
        gt = gt_cord_to_schema(gt_raw) if gt_format == "cord" else gt_self_to_schema(gt_raw)
        ppath = os.path.join(PRED, f"pred_{stem}.json")
        if not os.path.exists(ppath):
            continue
        pr = json.load(open(ppath))
        pred = pr.get("prediction") or {}

        # scalar field errors
        for f in SCALAR_FIELDS:
            res = compare_scalar(pred.get(f), gt.get(f))
            if res:
                agg["scalar"][f][res] += 1
                if res in ("fp", "fn", "wrong"):
                    out_rows.append({"doc": stem, "kind": "scalar", "field": f, "res": res,
                                     "pred": pred.get(f), "gt": gt.get(f)})
        pp, gp = pred.get("payment") or {}, gt.get("payment") or {}
        for f in PAY_FIELDS:
            res = compare_scalar(pp.get(f), gp.get(f))
            if res:
                agg["pay"][f][res] += 1
                if res in ("fp", "fn", "wrong"):
                    out_rows.append({"doc": stem, "kind": "pay", "field": f, "res": res,
                                     "pred": pp.get(f), "gt": gp.get(f)})

        # item-level
        stats, n_gt, n_pred, name_tp, missing = compare_items(
            pred.get("menu_items") or [], gt.get("menu_items") or [])
        for f in ITEM_FIELDS:
            for k in ("fp", "fn", "wrong"):
                if stats[f][k]:
                    agg["item"][f][k] += stats[f][k]
        # detailed item mismatch reasons
        gmap = defaultdict(list)
        for i, gi in enumerate(gt.get("menu_items") or []):
            gmap[name_norm(gi.get("name"))].append(i)
        used = set()
        for pi in pred.get("menu_items") or []:
            key = name_norm(pi.get("name"))
            match = None
            if key in gmap and gmap[key]:
                match = gmap[key].pop(0); used.add(match)
            if match is None:
                out_rows.append({"doc": stem, "kind": "item", "field": "name", "res": "fp_extra",
                                 "pred": pi.get("name"), "gt": None})
        for i, gi in enumerate(gt.get("menu_items") or []):
            if i not in used:
                out_rows.append({"doc": stem, "kind": "item", "field": "name", "res": "fn_missing",
                                 "pred": None, "gt": gi.get("name")})
        if missing:
            agg["missing_names"] += missing


def main():
    agg = {"scalar": {f: Counter() for f in SCALAR_FIELDS},
           "pay": {f: Counter() for f in PAY_FIELDS},
           "item": {f: Counter() for f in ITEM_FIELDS},
           "missing_names": []}
    rows = []
    analyze_split("cord", "data/cord_pool/manifest_test.json", "cord", rows, agg)
    analyze_split("self", "data/self_scan/manifest.json", "self", rows, agg)

    print("=" * 70)
    print("ERROR CATEGORIES (all docs)")
    print("=" * 70)
    print("\n--- SCALAR (subtotal/discount/tax/service_charge/total) ---")
    for f in SCALAR_FIELDS:
        c = agg["scalar"][f]
        if sum(c.values()):
            print(f"  {f:<16} {dict(c)}")
    print("\n--- PAYMENT ---")
    for f in PAY_FIELDS:
        c = agg["pay"][f]
        if sum(c.values()):
            print(f"  {f:<16} {dict(c)}")
    print("\n--- ITEM fields ---")
    for f in ITEM_FIELDS:
        c = agg["item"][f]
        if sum(c.values()):
            print(f"  {f:<16} {dict(c)}")

    print("\n--- Missing item names (GT items model never produced) ---")
    mc = Counter(agg["missing_names"])
    for name, cnt in mc.most_common(30):
        print(f"  x{cnt}  {name}")

    print("\n--- Per-doc errors (doc | kind | field | res | pred | gt) ---")
    bydoc = defaultdict(list)
    for r in rows:
        bydoc[r["doc"]].append(r)
    for doc in sorted(bydoc):
        print(f"\n[{doc}] ({len(bydoc[doc])} errors)")
        for r in bydoc[doc][:12]:
            print(f"   {r['kind']:6} {r['field']:<16} {r['res']:<10} pred={r['pred']!r} gt={r['gt']!r}")

    json.dump(rows, open(os.path.join(BASE, "results", "error_analysis.json"), "w"), indent=2)
    print(f"\nSaved results/error_analysis.json ({len(rows)} rows)")


if __name__ == "__main__":
    main()
