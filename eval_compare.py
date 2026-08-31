"""Compare 5 dev predictions vs CORD GT. Output: per-field comparison table."""
import json, os, re

BASE = r"C:\Users\ASUS\Documents\lamarankerja\datasaur\data"
IDXS = [14, 94, 28, 0, 75]

def norm_num(s):
    """CORD prices use ',' or '.' as thousand separators -> int"""
    if s is None or s == "": return None
    t = str(s).replace(",", "").replace(".", "").strip()
    if t == "" or not t.lstrip("-").isdigit(): return None
    return int(t)

def name_norm(s):
    if not s: return ""
    return re.sub(r"[^a-z0-9]+", "", s.lower())

def fmt(x):
    return "-" if x is None else f"{x:,}"

def main():
    rows = []
    for idx in IDXS:
        pred = json.load(open(os.path.join(BASE, "dev_predictions", f"pred_{idx:03d}.json")))
        gt = json.load(open(os.path.join(BASE, "dev_sample", f"cord_{idx:03d}.json")))
        p = pred["prediction"]; g = gt["gt_parse"]
        gmenu = g.get("menu", [])
        if isinstance(gmenu, dict):
            gmenu = [gmenu]
        gsub = g.get("sub_total", {}); gtot = g.get("total", {})

        print("=" * 70)
        print(f"idx {idx:03d}  latency {pred['latency_s']:.1f}s  model {pred['model']}")
        print("=" * 70)

        # --- menu items ---
        gmap = {name_norm(m.get("nm")): m for m in gmenu}
        used = set()
        print(f"{'#':<2} {'GT name':<28} {'PRED name':<28} {'qty':<4} {'u.price':<9} {'amount':<9}  status")
        for i, mi in enumerate(p["menu_items"]):
            key = name_norm(mi.get("name"))
            gm = gmap.get(key)
            if gm is None:
                # try loose match: prefix match
                for k, v in gmap.items():
                    if k and (k in key or key in k):
                        gm = v; key = k; break
            if gm is None:
                print(f"{i:<2} {'?':<28} {mi.get('name',''):<28} {mi.get('quantity')} {fmt(mi.get('unit_price'))} {fmt(mi.get('amount'))}  EXTRA")
                continue
            used.add(id(gm))
            gq = norm_num(gm.get("cnt")); gline = norm_num(gm.get("itemsubtotal")) or norm_num(gm.get("price"))
            # CORD `price` = LINE TOTAL (cnt>1: 194,000 utk 2x). unit price = line/cnt.
            gu = (gline // gq) if (gq and gline and gline % gq == 0) else None
            pn = norm_num(mi.get("quantity")); pp = norm_num(mi.get("unit_price")); pa = norm_num(mi.get("amount"))
            issues = []
            if pn != gq: issues.append(f"qty pred={pn} gt={gq}")
            if pp != gu: issues.append(f"uprice pred={fmt(pp)} gt={fmt(gu)}")
            if pa != gline: issues.append(f"amt pred={fmt(pa)} gt(line)={fmt(gline)}")
            print(f"{i:<2} {gm.get('nm',''):<28} {mi.get('name',''):<28} {mi.get('quantity')} {fmt(pp)} {fmt(pa)}  {'OK' if not issues else '; '.join(issues)}")
        missing = [m.get("nm") for m in gmenu if id(m) not in used]
        if missing:
            print(f"  MISSING from GT: {missing}")

        # --- totals ---
        def cmp(label, pv, gv):
            pn = norm_num(pv); gn = norm_num(gv)
            # discount bisa negatif di GT ("-60.000"), bandingkan nilai absolut
            if pn is not None: pn = abs(pn)
            if gn is not None: gn = abs(gn)
            mark = "OK" if pn == gn else f"MISMATCH (gt={fmt(gn)})"
            print(f"  {label:<16} pred={fmt(pn):<10} {mark}")
            return pn == gn

        print("  --- money fields ---")
        cmp("subtotal", p.get("subtotal"), gsub.get("subtotal_price"))
        cmp("discount", p.get("discount"), gsub.get("discount_price"))
        cmp("tax", p.get("tax"), gsub.get("tax_price"))
        cmp("service_charge", p.get("service_charge"), gsub.get("service_price"))
        cmp("total", p.get("total"), gtot.get("total_price"))

        # --- payment ---
        print("  --- payment ---")
        pay = p.get("payment") or {}
        pm = pay.get("method")
        gt_cash = norm_num(gtot.get("cashprice")); gt_cc = norm_num(gtot.get("creditcardprice"))
        gt_em = norm_num(gtot.get("emoneyprice")); gt_ch = norm_num(gtot.get("changeprice"))
        if gt_cash is not None: gt_method = "cash"; gt_amt = gt_cash
        elif gt_cc is not None: gt_method = "credit_card"; gt_amt = gt_cc
        elif gt_em is not None: gt_method = "emoney"; gt_amt = gt_em
        else: gt_method = None; gt_amt = None
        print(f"  method    pred={pm} gt={gt_method}  {'OK' if pm == gt_method else 'MISMATCH'}")
        cmp("amount_paid", pay.get("amount_paid"), gt_amt)
        cmp("change", pay.get("change"), gt_ch)
        rows.append((idx, pred["latency_s"]))
    print("\n" + "=" * 70)
    print("LATENCY SUMMARY")
    for idx, lat in rows:
        print(f"  idx {idx:03d}: {lat:.1f}s")

if __name__ == "__main__":
    main()
