"""Eval harness: field-level P/R/F1, error typology, abstention, cost/latency.

Supports 2 GT formats:
  - CORD: gt_parse {menu[], sub_total{}, total{}}
  - self-scan: {prediction{...}, meta{}} (schema format)

Usage:
  python eval_harness.py --manifest data/cord_pool/manifest_test.json --pred-dir data/predictions
  python eval_harness.py --manifest data/self_scan/manifest.json --pred-dir data/predictions --gt-format self
"""
import json, os, argparse, re
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))

def to_num(s):
    """Parse price strings: 'Rp 111000', '-60.000', '24,000', 25000, None -> int or None."""
    if s is None: return None
    if isinstance(s, (int, float)): return int(s)
    t = str(s).strip().replace("Rp", "").replace("rp", "").replace(" ", "")
    t = t.replace(",", "").replace(".", "")
    neg = t.startswith("-")
    if neg: t = t[1:]
    if t == "" or not t.isdigit(): return None
    v = int(t)
    return -v if neg else v

def name_norm(s):
    if not s: return ""
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())

# ---------- GT adapters ----------
def gt_cord_to_schema(gt):
    """Convert CORD gt_parse to our schema shape."""
    gp = gt["gt_parse"]
    menu = gp.get("menu", [])
    if isinstance(menu, dict): menu = [menu]
    items = []
    for m in menu:
        cnt = to_num(m.get("cnt"))
        line = to_num(m.get("itemsubtotal")) or to_num(m.get("price"))
        # CORD `price` = LINE TOTAL (cnt>1 => 194,000 for 2x)
        uprice = (line // cnt) if (cnt and line and line % cnt == 0) else None
        items.append({
            "name": m.get("nm"), "quantity": cnt, "unit_price": uprice, "amount": line,
        })
    sub = gp.get("sub_total", {}); tot = gp.get("total", {})
    pay = {}
    for pv, pname in [(tot.get("cashprice"), "cash"), (tot.get("creditcardprice"), "credit_card"),
                      (tot.get("emoneyprice"), "emoney")]:
        v = to_num(pv)
        if v:
            pay = {"method": pname, "amount_paid": v}
            break
    return {
        "menu_items": items,
        "subtotal": to_num(sub.get("subtotal_price")),
        "discount": abs(to_num(sub.get("discount_price"))) if to_num(sub.get("discount_price")) is not None else None,
        "tax": to_num(sub.get("tax_price")),
        "service_charge": to_num(sub.get("service_price")),
        "total": to_num(tot.get("total_price")),
        "payment": {
            "method": pay.get("method"), "amount_paid": pay.get("amount_paid"),
            "change": to_num(tot.get("changeprice")),
        },
    }

def gt_self_to_schema(gt):
    p = gt["prediction"]
    return {
        "menu_items": [dict(mi) for mi in p.get("menu_items", [])],
        "subtotal": p.get("subtotal"), "discount": p.get("discount"), "tax": p.get("tax"),
        "service_charge": p.get("service_charge"), "total": p.get("total"),
        "payment": dict(p.get("payment") or {}),
    }

# ---------- field comparison ----------
SCALAR_FIELDS = ["subtotal", "discount", "tax", "service_charge", "total"]
PAY_FIELDS = ["method", "amount_paid", "change"]
ITEM_FIELDS = ["quantity", "unit_price", "amount"]

def compare_scalar(pv, gv):
    """Return ('tp'|'fp'|'fn'|'wrong'), or None if both absent."""
    # payment.method is a string enum; others are numbers
    if isinstance(pv, str) or isinstance(gv, str):
        pn = (pv or "").strip().lower() or None
        gn = (gv or "").strip().lower() or None
        if pn is None and gn is None: return None
        if gn is None: return "fp"
        if pn is None: return "fn"
        if pn == gn: return "tp"
        return "wrong"
    pn = to_num(pv); gn = to_num(gv)
    if pn is None and gn is None: return None  # both absent - skip
    if gn is None: return "fp"   # predicted but not in GT
    if pn is None: return "fn"   # GT has value, model abstained
    if pn == gn: return "tp"
    return "wrong"

def compare_items(pred_items, gt_items):
    """Item-level: match by name, then per-field compare."""
    used = set()
    stats = {f: {"tp": 0, "fp": 0, "fn": 0, "wrong": 0, "n": 0} for f in ITEM_FIELDS}
    # GT by normalized name
    gmap = {}
    for i, gi in enumerate(gt_items):
        key = name_norm(gi.get("name"))
        if key: gmap.setdefault(key, []).append(i)
    g_index = {i: gi for i, gi in enumerate(gt_items)}
    n_gt = len(gt_items); n_pred = len(pred_items)
    item_name_tp = 0
    for pi in pred_items:
        key = name_norm(pi.get("name"))
        match = None
        if key and key in gmap and gmap[key]:
            match = gmap[key].pop(0)
            used.add(match)
            item_name_tp += 1
        for f in ITEM_FIELDS:
            if match is not None:
                gv = g_index[match].get(f)
                res = compare_scalar(pi.get(f), gv)
            else:
                res = "fp"  # extra item - all its fields are FP
            if res:
                stats[f][res] += 1
            stats[f]["n"] += 1
    # missing items in GT not predicted
    missing_names = [g_index[i]["name"] for i in range(n_gt) if i not in used]
    return stats, n_gt, n_pred, item_name_tp, missing_names

# ---------- main ----------
def run(manifest_path, pred_dir, gt_format):
    manifest = json.load(open(os.path.join(BASE, manifest_path)))
    results = []
    scalar_agg = {f: defaultdict(int) for f in SCALAR_FIELDS}
    pay_agg = {f: defaultdict(int) for f in PAY_FIELDS}
    item_agg = {f: defaultdict(int) for f in ITEM_FIELDS}
    n_items_gt = 0; n_items_pred = 0; n_item_name_tp = 0
    n_total = 0; total_ok = 0
    latencies = []; abstain_total = 0
    per_doc = []

    for e in manifest:
        idx = e["idx"]; img = e["image"]
        stem = os.path.splitext(img)[0]  # cord_001 / self_001
        # GT (same dir as manifest, named <stem>.json)
        gt_raw = json.load(open(os.path.join(BASE, os.path.dirname(manifest_path), f"{stem}.json")))
        if gt_format == "cord":
            gt = gt_cord_to_schema(gt_raw)
        else:
            gt = gt_self_to_schema(gt_raw)
        # Pred: data/predictions/pred_<stem>.json
        pred_path = os.path.join(BASE, pred_dir, f"pred_{stem}.json")
        if not os.path.exists(pred_path):
            print(f"  !! no prediction for {idx} ({img})")
            continue
        pr = json.load(open(pred_path))
        pred = pr.get("prediction") or {}
        lat = pr.get("latency_s")
        if lat is not None: latencies.append(lat)

        doc = {"idx": idx, "img": img, "fields": {}}
        # scalars
        for f in SCALAR_FIELDS:
            res = compare_scalar(pred.get(f), gt.get(f))
            if res: scalar_agg[f][res] += 1
            doc["fields"][f] = res
            if f == "total":
                n_total += 1
                if res == "tp": total_ok += 1
        # payment
        pp = pred.get("payment") or {}; gp_ = gt.get("payment") or {}
        for f in PAY_FIELDS:
            res = compare_scalar(pp.get(f), gp_.get(f))
            if res: pay_agg[f][res] += 1
            doc["fields"][f"payment.{f}"] = res
        # items
        stats, n_gt, n_pred, name_tp, missing = compare_items(
            pred.get("menu_items") or [], gt.get("menu_items") or [])
        n_items_gt += n_gt; n_items_pred += n_pred; n_item_name_tp += name_tp
        for f in ITEM_FIELDS:
            for k, v in stats[f].items():
                item_agg[f][k] += v
        doc["n_items_gt"] = n_gt; doc["n_items_pred"] = n_pred
        doc["missing_items"] = missing
        # abstention: total is null in pred but present in GT
        if to_num(pred.get("total")) is None and to_num(gt.get("total")) is not None:
            abstain_total += 1
        per_doc.append(doc)
        print(f"{idx:>6} {img:<22} total={doc['fields'].get('total')} items {n_gt}->{n_pred}"
              + (f" MISSING: {missing}" if missing else ""))

    # ---------- aggregate ----------
    def prf(cnt):
        tp, fp, fn = cnt["tp"], cnt["fp"], cnt["fn"]
        prec = tp/(tp+fp) if (tp+fp) else 0
        rec = tp/(tp+fn) if (tp+fn) else 0
        f1 = 2*prec*rec/(prec+rec) if (prec+rec) else 0
        return prec, rec, f1

    print("\n" + "="*70)
    print(f"EVAL SUMMARY ({gt_format})  docs={len(per_doc)}")
    print("="*70)
    print(f"{'field':<18} {'P':>6} {'R':>6} {'F1':>6}  tp fp fn wrong")
    all_tp = all_fp = all_fn = all_wrong = 0
    for f in SCALAR_FIELDS + [f"payment.{x}" for x in PAY_FIELDS] + [f"item.{x}" for x in ITEM_FIELDS]:
        if f.startswith("item."):
            cnt = item_agg[f[5:]]
        elif f.startswith("payment."):
            cnt = pay_agg[f[8:]]
        else:
            cnt = scalar_agg[f]
        p, r, f1 = prf(cnt)
        all_tp += cnt["tp"]; all_fp += cnt["fp"]; all_fn += cnt["fn"]; all_wrong += cnt["wrong"]
        print(f"{f:<18} {p:6.2f} {r:6.2f} {f1:6.2f}  {cnt['tp']:>2} {cnt['fp']:>2} {cnt['fn']:>2} {cnt['wrong']:>3}")

    p, r, f1 = prf({"tp": all_tp, "fp": all_fp, "fn": all_fn})
    print("-"*70)
    print(f"{'MACRO/FIELD-LEVEL':<18} {p:6.2f} {r:6.2f} {f1:6.2f}  ({all_tp} tp / {all_fp} fp / {all_fn} fn / {all_wrong} wrong)")

    # item name matching
    if n_items_gt:
        name_p = n_item_name_tp/n_items_pred if n_items_pred else 0
        name_r = n_item_name_tp/n_items_gt
        name_f1 = 2*name_p*name_r/(name_p+name_r) if (name_p+name_r) else 0
        print(f"\nItem name match: P={name_p:.2f} R={name_r:.2f} F1={name_f1:.2f} ({n_item_name_tp}/{n_items_gt} gt matched, {n_items_pred} pred)")
    if n_total:
        print(f"total exact: {total_ok}/{n_total} ({total_ok/n_total:.2f})")

    # abstention & latency
    print(f"\nTotal-field abstention (GT present, pred null): {abstain_total}/{n_total}")
    if latencies:
        latencies.sort()
        n = len(latencies)
        print(f"Latency: mean={sum(latencies)/n:.1f}s median={latencies[n//2]:.1f}s "
              f"min={latencies[0]:.1f}s max={latencies[-1]:.1f}s")

    return per_doc

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--pred-dir", default="data/predictions")
    ap.add_argument("--gt-format", default="cord", choices=["cord", "self"])
    args = ap.parse_args()
    run(args.manifest, args.pred_dir, args.gt_format)
