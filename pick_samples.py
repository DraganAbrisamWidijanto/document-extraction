import json, os

BASE = r"C:\Users\ASUS\Documents\lamarankerja\datasaur\data\dev_sample"
manifest = json.load(open(os.path.join(BASE, "manifest.json")))

rows = []
for m in manifest:
    gt = json.load(open(os.path.join(BASE, m["gt"])))
    p = gt.get("gt_parse", {})
    menu = p.get("menu", [])
    n_items = len(menu) if isinstance(menu, list) else 1
    has_tax = "tax_price" in (p.get("sub_total") or {})
    has_disc = "discount_price" in (p.get("sub_total") or {})
    has_credit = "creditcardprice" in (p.get("total") or {})
    has_cash = "cashprice" in (p.get("total") or {})
    rows.append({
        "idx": m["idx"], "size": m["size"], "n_items": n_items,
        "tax": has_tax, "disc": has_disc, "credit": has_credit, "cash": has_cash,
        "fields": m["fields"],
    })

# Print all with features
for r in rows:
    tags = []
    if r["tax"]: tags.append("TAX")
    if r["disc"]: tags.append("DISC")
    if r["credit"]: tags.append("CC")
    if r["cash"]: tags.append("CASH")
    if r["n_items"] > 1: tags.append(f"{r['n_items']}items")
    print(f"idx={r['idx']:3d} size={r['size']} {' '.join(tags)}")

print("\n=== CANDIDATES ===")
# diverse: high-res photo, low-res, multi-item+tax, credit card, single item no subtotal
