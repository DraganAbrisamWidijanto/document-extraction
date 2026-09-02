"""Eval harness: field-level P/R/F1, error typology, confidence, abstention, cost.

Supports 3 GT formats:
  - CORD: gt_parse {menu[], sub_total{}, total{}}
  - self-scan: {prediction{...}, meta{}} (schema format)
  - kleister: {prediction{...}, meta{}} (charity report, 8 fields)

Outputs:
  - Human-readable summary (stdout + results/eval_<split>.txt)
  - JSON scorecard (results/scorecard_<split>.json) — for A/B comparison

Usage:
  python eval_harness.py --manifest data/cord_pool/manifest_test.json --pred-dir data/predictions
  python eval_harness.py --manifest data/self_scan/manifest.json --pred-dir data/predictions --gt-format self
  python eval_harness.py --manifest data/kleister/manifest.json --pred-dir data/predictions --gt-format kleister
"""
import argparse
import json
import os
import re
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

SCALAR_FIELDS = ["subtotal", "discount", "tax", "service_charge", "total"]
PAY_FIELDS = ["method", "amount_paid", "change"]
ITEM_FIELDS = ["quantity", "unit_price", "amount"]

# Kleister charity-report schema (GT adapter maps address__* -> address_*)
KLEISTER_FIELDS = [
    "charity_name", "charity_number",
    "address_post_town", "address_postcode", "address_street_line",
    "income_annually_in_british_pounds", "spending_annually_in_british_pounds",
    "report_date",
]
KLEISTER_NUMERIC = {"income_annually_in_british_pounds", "spending_annually_in_british_pounds"}


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
def to_num(s):
    """Parse price strings: 'Rp 111000', '-60.000', '24,000', '365000.00', 25000 -> int or None.

    Handles:
      - Thousand separators: both '.' (46.000 → 46000) and ',' (46,000 → 46000)
      - Decimal point: '365000.00' → 365000 (dot followed by 2 digits = decimal)
      - Currency prefix: 'Rp', 'rp', spaces
      - Negative sign: '-60000' → -60000
      - Lists: CORD sometimes stores a value as ['100,000', '100,000'] or
        ['6,818', '0'] (multiple tender/tax rows) → use the first non-zero
        parseable element.
    """
    if s is None:
        return None
    if isinstance(s, (list, tuple)):
        for el in s:
            v = to_num(el)
            if v:
                return v
        return None
    if isinstance(s, (int, float)):
        return int(s)
    t = str(s).strip().replace("Rp", "").replace("rp", "").replace(" ", "")
    # strip decorative prefixes like '*' (CORD uses '*110000' for some values)
    t = t.lstrip("*")
    neg = t.startswith("-")
    if neg:
        t = t[1:]
    if t == "":
        return None
    # Detect decimal point: dot with exactly 2 trailing digits (e.g., "365000.00")
    if '.' in t and t.rfind('.') >= len(t) - 3:
        # Strip thousand-separator commas, then parse as float
        t = t.replace(",", "")
        try:
            v = int(float(t))
            return -v if neg else v
        except ValueError:
            return None
    # No decimal: strip all thousand separators (dots and commas)
    t = t.replace(",", "").replace(".", "")
    if not t.isdigit():
        return None
    v = int(t)
    return -v if neg else v


def name_norm(s):
    if not s:
        return ""
    t = str(s).lower()
    # strip leading item code (e.g. "0571-1854 blus wanita" -> "blus wanita")
    t = re.sub(r"^\d{2,}-\d{2,}\s+", "", t)
    return re.sub(r"[^a-z0-9]+", "", t)


# ---------------------------------------------------------------------------
# GT adapters
# ---------------------------------------------------------------------------
def gt_cord_to_schema(gt):
    """Convert CORD gt_parse to our schema shape."""
    gp = gt["gt_parse"]
    menu = gp.get("menu", [])
    if isinstance(menu, dict):
        menu = [menu]
    items = []
    for m in menu:
        cnt = to_num(m.get("cnt"))
        line = to_num(m.get("itemsubtotal")) or to_num(m.get("price"))
        uprice = (line // cnt) if (cnt and line and line % cnt == 0) else None
        items.append({"name": m.get("nm"), "quantity": cnt, "unit_price": uprice, "amount": line})
    sub, tot = gp.get("sub_total", {}), gp.get("total", {})
    # total_price fallback: if missing, try total_etc, cashprice, changeprice, or compute from items
    total_val = to_num(tot.get("total_price"))
    if total_val is None:
        total_val = to_num(tot.get("total_etc"))
    if total_val is None:
        total_val = to_num(tot.get("cashprice"))
    if total_val is None:
        total_val = to_num(tot.get("changeprice"))
    if total_val is None:
        cash = to_num(tot.get("cashprice"))
        change = to_num(tot.get("changeprice"))
        if cash is not None and change is not None:
            total_val = abs(cash - change)
    pay = {}
    for pv, pname in [(tot.get("debitprice"), "debit"), (tot.get("cashprice"), "cash"),
                      (tot.get("creditcardprice"), "credit_card"),
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
        "total": total_val,
        "payment": {"method": pay.get("method"), "amount_paid": pay.get("amount_paid"),
                    "change": to_num(tot.get("changeprice"))},
    }


def gt_self_to_schema(gt):
    p = gt["prediction"]
    return {
        "menu_items": [dict(mi) for mi in p.get("menu_items", [])],
        "subtotal": p.get("subtotal"), "discount": p.get("discount"), "tax": p.get("tax"),
        "service_charge": p.get("service_charge"), "total": p.get("total"),
        "payment": dict(p.get("payment") or {}),
    }


def gt_kleister_to_schema(gt):
    """Kleister GT is already in our schema field names (address_post_town etc)."""
    p = gt["prediction"]
    return {f: p.get(f) for f in KLEISTER_FIELDS}


# ---------------------------------------------------------------------------
# Field comparison
# ---------------------------------------------------------------------------
def compare_scalar(pv, gv):
    """Return 'tp'|'fp'|'fn'|'wrong', or None if both absent."""
    if isinstance(pv, str) or isinstance(gv, str):
        def _norm(s):
            t = (s or "").strip().lower()
            # normalize typographic apostrophes to ASCII
            return t.replace("\u2019", "'").replace("\u2018", "'")
        pn = _norm(pv) or None
        gn = _norm(gv) or None
        if pn is None and gn is None:
            return None
        if gn is None:
            return "fp"
        if pn is None:
            return "fn"
        return "tp" if pn == gn else "wrong"
    pn, gn = to_num(pv), to_num(gv)
    if pn is None and gn is None:
        return None
    if gn is None:
        return "fp"
    if pn is None:
        return "fn"
    if pn == gn:
        return "tp"
    return "wrong"


# ---------------------------------------------------------------------------
# Kleister GT normalization (eval fairness, not extraction)
# ---------------------------------------------------------------------------
LEGAL_SUFFIXES = {"ltd", "ltd.", "limited", "co", "co.", "company", "plc", "cic"}
_NUM_WORD = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
             "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def _name_tokens(s):
    """Lowercase tokens with legal-form suffixes dropped."""
    toks = re.findall(r"[a-z0-9]+", (s or "").lower())
    return [t for t in toks if t not in LEGAL_SUFFIXES]


def _street_tokens(s):
    """Lowercase street tokens: numeric ranges collapse to their lower bound,
    number-words map to digits ('2-3 HEATHGATE' ~ '2 heathgate')."""
    out = []
    for tok in re.findall(r"[a-z0-9]+", (s or "").lower()):
        m = re.match(r"(\d+)-(\d+)$", tok)
        if m:
            out.append(m.group(1))
            continue
        out.append(_NUM_WORD.get(tok, tok))
    return out


def _compare_kleister_name(pv, gv):
    pn, gn = _name_tokens(pv), _name_tokens(gv)
    if pn == gn:
        return "tp"
    # one-sided abbreviation: GT drops tokens the pred keeps (e.g. official
    # long form 'construction industry training board' vs GT 'citb' handled
    # separately) — require the shorter to be a prefix/subsequence of the longer
    shorter, longer = (pn, gn) if len(pn) <= len(gn) else (gn, pn)
    it = iter(longer)
    if all(tok in it for tok in shorter):
        return "tp"
    # initialism: GT short form like 'citb' == first letters of the long form
    if len(shorter) == 1 and len(longer) > 1:
        if shorter[0] == "".join(t[0] for t in longer if t):
            return "tp"
    return "wrong"


def _compare_kleister_street(pv, gv):
    pn, gn = _street_tokens(pv), _street_tokens(gv)
    if not pn and not gn:
        return None
    if not gn:
        return "fp"   # pred present, GT absent
    if not pn:
        return "fn"
    shorter, longer = (pn, gn) if len(pn) <= len(gn) else (gn, pn)
    it = iter(longer)
    if all(tok in it for tok in shorter):
        return "tp"
    return "wrong"


def compare_items(pred_items, gt_items):
    """Item-level: match by normalized name, then per-field compare.
    Returns (stats, n_gt, n_pred, name_tp, missing_names)."""
    used = set()
    stats = {f: {"tp": 0, "fp": 0, "fn": 0, "wrong": 0, "n": 0} for f in ITEM_FIELDS}
    gmap = {}
    for i, gi in enumerate(gt_items):
        key = name_norm(gi.get("name"))
        if key:
            gmap.setdefault(key, []).append(i)
    g_index = {i: gi for i, gi in enumerate(gt_items)}
    n_gt, n_pred = len(gt_items), len(pred_items)
    name_tp = 0
    for pi in pred_items:
        key = name_norm(pi.get("name"))
        match = None
        if key and key in gmap and gmap[key]:
            match = gmap[key].pop(0)
            used.add(match)
            name_tp += 1
        for f in ITEM_FIELDS:
            if match is not None:
                res = compare_scalar(pi.get(f), g_index[match].get(f))
            else:
                res = "fp"
            if res:
                stats[f][res] += 1
            stats[f]["n"] += 1
    missing = [g_index[i]["name"] for i in range(n_gt) if i not in used]
    return stats, n_gt, n_pred, name_tp, missing


# ---------------------------------------------------------------------------
# Confidence helpers
# ---------------------------------------------------------------------------
def pred_confidence(pred, field):
    """Look up model-reported confidence for a field (top-level or payment.*)."""
    conf = pred.get("confidence") or {}
    if field.startswith("payment."):
        return conf.get(field)
    return conf.get(field)


def item_confidence(item, field):
    c = item.get("confidence") or {}
    return c.get(field)


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


# ---------------------------------------------------------------------------
# Metrics aggregation
# ---------------------------------------------------------------------------
def prf(cnt):
    tp, fp, fn = cnt["tp"], cnt["fp"], cnt["fn"]
    prec = tp / (tp + fp) if (tp + fp) else 0
    rec = tp / (tp + fn) if (tp + fn) else 0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0
    return prec, rec, f1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run(manifest_path, pred_dir, gt_format):
    manifest = json.load(open(os.path.join(BASE_DIR, manifest_path)))
    results = []
    scalar_agg = {f: defaultdict(int) for f in SCALAR_FIELDS}
    pay_agg = {f: defaultdict(int) for f in PAY_FIELDS}
    item_agg = {f: defaultdict(int) for f in ITEM_FIELDS}
    n_items_gt = n_items_pred = n_item_name_tp = 0
    n_total = total_ok = 0
    latencies, abstain_total = [], 0
    per_doc = []
    # confidence accumulation per field: {field: {label: [confidences]}}
    conf_acc = defaultdict(lambda: defaultdict(list))
    cost_total = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}

    for e in manifest:
        idx, img = e["idx"], e["image"]
        stem = os.path.splitext(img)[0]
        gt_raw = json.load(open(os.path.join(BASE_DIR, os.path.dirname(manifest_path), f"{stem}.json")))
        gt = gt_cord_to_schema(gt_raw) if gt_format == "cord" else gt_self_to_schema(gt_raw)

        pred_path = os.path.join(BASE_DIR, pred_dir, f"pred_{stem}.json")
        if not os.path.exists(pred_path):
            print(f"  !! no prediction for {idx} ({img})")
            continue
        pr = json.load(open(pred_path))
        pred = pr.get("prediction") or {}
        if pr.get("latency_s") is not None:
            latencies.append(pr["latency_s"])
        # cost
        usage = pr.get("usage") or {}
        cost_total["prompt_tokens"] += int(usage.get("prompt_tokens", 0))
        cost_total["completion_tokens"] += int(usage.get("completion_tokens", 0))
        cost_total["cost_usd"] += float(pr.get("cost_usd") or 0)

        doc = {"idx": idx, "img": img, "fields": {}}
        for f in SCALAR_FIELDS:
            res = compare_scalar(pred.get(f), gt.get(f))
            if res:
                scalar_agg[f][res] += 1
            doc["fields"][f] = res
            if res:
                conf_acc[f][res].append(pred_confidence(pred, f))
            if f == "total":
                n_total += 1
                if res == "tp":
                    total_ok += 1
        pp, gp = pred.get("payment") or {}, gt.get("payment") or {}
        for f in PAY_FIELDS:
            res = compare_scalar(pp.get(f), gp.get(f))
            if res:
                pay_agg[f][res] += 1
            doc["fields"][f"payment.{f}"] = res
            if res:
                conf_acc[f"payment.{f}"][res].append(pred_confidence(pred, f))
        stats, n_gt, n_pred, name_tp, missing = compare_items(
            pred.get("menu_items") or [], gt.get("menu_items") or [])
        n_items_gt += n_gt
        n_items_pred += n_pred
        n_item_name_tp += name_tp
        for f in ITEM_FIELDS:
            for k, v in stats[f].items():
                item_agg[f][k] += v
        # item confidence: match GT item by name for confidence correlation
        pred_by_name = {name_norm(i.get("name")): i for i in pred.get("menu_items") or []}
        for gi in gt.get("menu_items") or []:
            pi = pred_by_name.get(name_norm(gi.get("name")))
            if pi is None:
                continue
            for f in ITEM_FIELDS:
                res = compare_scalar(pi.get(f), gi.get(f))
                if res:
                    conf_acc[f"item.{f}"][res].append(item_confidence(pi, f))
        doc["n_items_gt"], doc["n_items_pred"], doc["missing_items"] = n_gt, n_pred, missing
        if to_num(pred.get("total")) is None and to_num(gt.get("total")) is not None:
            abstain_total += 1
        per_doc.append(doc)
        print(f"{idx:>6} {img:<22} total={doc['fields'].get('total')} items {n_gt}->{n_pred}"
              + (f" MISSING: {missing}" if missing else ""))

    # ---------- aggregate ----------
    lines = []
    lines.append("\n" + "=" * 70)
    lines.append(f"EVAL SUMMARY ({gt_format})  docs={len(per_doc)}")
    lines.append("=" * 70)
    lines.append(f"{'field':<18} {'P':>6} {'R':>6} {'F1':>6}  tp fp fn wrong  conf_tp conf_wrong")
    all_tp = all_fp = all_fn = all_wrong = 0
    field_f1 = {}
    for f in SCALAR_FIELDS + [f"payment.{x}" for x in PAY_FIELDS] + [f"item.{x}" for x in ITEM_FIELDS]:
        cnt = item_agg[f[5:]] if f.startswith("item.") else pay_agg[f[8:]] if f.startswith("payment.") else scalar_agg[f]
        # Fixed label set: a label that never occurred counts as 0, so field F1
        # and the macro average stay comparable across runs and splits.
        for lbl in ("tp", "fp", "fn", "wrong"):
            cnt.setdefault(lbl, 0)
        p, r, f1 = prf(cnt)
        all_tp += cnt["tp"]
        all_fp += cnt["fp"]
        all_fn += cnt["fn"]
        all_wrong += cnt["wrong"]
        field_f1[f] = round(f1, 3)
        ct, cw = _avg(conf_acc[f]["tp"]), _avg(conf_acc[f]["wrong"])
        lines.append(f"{f:<18} {p:6.2f} {r:6.2f} {f1:6.2f}  {cnt['tp']:>2} {cnt['fp']:>2} {cnt['fn']:>2} {cnt['wrong']:>3}  {ct if ct is not None else '-':>7} {cw if cw is not None else '-':>9}")

    micro_p, micro_r, micro_f1 = prf({"tp": all_tp, "fp": all_fp, "fn": all_fn})
    # Macro over EVALUABLE fields only: fields whose GT has at least one instance
    # (tp/fn/wrong all require GT present). The evaluable set is determined by the
    # dataset alone — stable across versions — and excluded fields are listed.
    def agg_for(field):
        return (item_agg[field[5:]] if field.startswith("item.")
                else pay_agg[field[8:]] if field.startswith("payment.")
                else scalar_agg[field])
    gt_support = {f: agg_for(f)["tp"] + agg_for(f)["fn"] + agg_for(f)["wrong"] for f in field_f1}
    evaluable = [f for f in field_f1 if gt_support[f] > 0]
    excluded = [f for f in field_f1 if gt_support[f] == 0]
    macro_p = sum(prf(agg_for(f))[0] for f in evaluable) / len(evaluable)
    macro_r = sum(prf(agg_for(f))[1] for f in evaluable) / len(evaluable)
    macro_f1 = round(sum(field_f1[f] for f in evaluable) / len(evaluable), 3)
    lines.append("-" * 70)
    lines.append(f"{'MICRO/FIELD-LEVEL':<18} {micro_p:6.2f} {micro_r:6.2f} {micro_f1:6.2f}  ({all_tp} tp / {all_fp} fp / {all_fn} fn / {all_wrong} wrong)")
    exc_note = f"; no-GT fields excluded: {', '.join(excluded)}" if excluded else ""
    lines.append(f"{'MACRO (field avg)':<18} {macro_p:6.2f} {macro_r:6.2f} {macro_f1:6.2f}  (mean field F1 over {len(evaluable)} evaluable fields{exc_note})")

    if n_items_gt:
        name_p = n_item_name_tp / n_items_pred if n_items_pred else 0
        name_r = n_item_name_tp / n_items_gt
        name_f1 = 2 * name_p * name_r / (name_p + name_r) if (name_p + name_r) else 0
        lines.append(f"\nItem name match: P={name_p:.2f} R={name_r:.2f} F1={name_f1:.2f} "
                     f"({n_item_name_tp}/{n_items_gt} gt matched, {n_items_pred} pred)")
    if n_total:
        lines.append(f"total exact: {total_ok}/{n_total} ({total_ok/n_total:.2f})")

    # abstention per field (GT present, pred null)
    abstain_by_field = defaultdict(int)
    for d in per_doc:
        for f, res in d["fields"].items():
            if res == "fn":
                abstain_by_field[f] += 1
    if abstain_by_field:
        lines.append("\nAbstentions (GT present, pred null) per field: "
                     + ", ".join(f"{k}={v}" for k, v in sorted(abstain_by_field.items())))
    lines.append(f"Total-field abstention: {abstain_total}/{n_total}")

    if latencies:
        latencies.sort()
        n = len(latencies)
        lines.append(f"Latency: mean={sum(latencies)/n:.1f}s median={latencies[n//2]:.1f}s "
                     f"min={latencies[0]:.1f}s max={latencies[-1]:.1f}s")
    lines.append(f"Tokens: {cost_total['prompt_tokens']} in / {cost_total['completion_tokens']} out "
                 f"| cost: ${cost_total['cost_usd']:.6f}")

    out = "\n".join(lines)
    print(out)

    # save scorecard JSON + summary txt
    scorecard = {
        "split": gt_format, "docs": len(per_doc), "macro_f1": round(macro_f1, 3),
        "field_f1": field_f1, "total_exact": [total_ok, n_total],
        "item_name_f1": round(name_f1, 3) if n_items_gt else None,
        "latency": {"mean": round(sum(latencies)/len(latencies), 1), "median": round(latencies[len(latencies)//2], 1),
                    "min": round(latencies[0], 1), "max": round(latencies[-1], 1)} if latencies else None,
        "cost_usd": round(cost_total["cost_usd"], 6),
        "tokens": {"prompt": cost_total["prompt_tokens"], "completion": cost_total["completion_tokens"]},
        "abstentions": dict(abstain_by_field),
        "confidence": {f: {"tp": _avg(conf_acc[f]["tp"]), "wrong": _avg(conf_acc[f]["wrong"])}
                       for f in conf_acc},
    }
    with open(os.path.join(RESULTS_DIR, f"scorecard_{gt_format}.json"), "w") as f:
        json.dump(scorecard, f, indent=2)
    with open(os.path.join(RESULTS_DIR, f"eval_{gt_format}.txt"), "w") as f:
        f.write(out)
    with open(os.path.join(RESULTS_DIR, f"per_doc_{gt_format}.json"), "w") as f:
        json.dump(per_doc, f, indent=2)
    return scorecard


def run_kleister(manifest_path, pred_dir):
    """Eval loop for Kleister-Charity: 8 fields, mixed string/numeric compare."""
    manifest = json.load(open(os.path.join(BASE_DIR, manifest_path)))
    agg = {f: {"tp": 0, "fp": 0, "fn": 0, "wrong": 0} for f in KLEISTER_FIELDS}
    n_total = total_ok = 0
    latencies = []
    cost_total = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
    per_doc = []

    for e in manifest:
        idx, img = e["idx"], e["image"]
        gt_raw = json.load(open(os.path.join(BASE_DIR, os.path.dirname(manifest_path), f"{idx}.json")))
        gt = gt_kleister_to_schema(gt_raw)

        pred_path = os.path.join(BASE_DIR, pred_dir, f"pred_{idx}.json")
        if not os.path.exists(pred_path):
            print(f"  !! no prediction for {idx}")
            continue
        pr = json.load(open(pred_path))
        pred = pr.get("prediction") or {}
        if pr.get("latency_s") is not None:
            latencies.append(pr["latency_s"])
        usage = pr.get("usage") or {}
        cost_total["prompt_tokens"] += int(usage.get("prompt_tokens", 0))
        cost_total["completion_tokens"] += int(usage.get("completion_tokens", 0))
        cost_total["cost_usd"] += float(pr.get("cost_usd") or 0)

        doc = {"idx": idx, "img": img, "fields": {}}
        for f in KLEISTER_FIELDS:
            pv, gv = pred.get(f), gt.get(f)
            if f == "charity_name":
                res = _compare_kleister_name(pv, gv)
            elif f == "address_street_line":
                res = _compare_kleister_street(pv, gv)
            else:
                res = compare_scalar(pv, gv)
            if res:
                agg[f][res] += 1
            doc["fields"][f] = res
            if f == "charity_name":
                n_total += 1
                if res == "tp":
                    total_ok += 1

        per_doc.append(doc)
        print(f"{idx:>8} {img[:30]:<32} charity_name={doc['fields'].get('charity_name')}")

    # ---------- aggregate ----------
    lines = []
    lines.append("\n" + "=" * 70)
    lines.append(f"EVAL SUMMARY (kleister)  docs={len(per_doc)}")
    lines.append("=" * 70)
    lines.append(f"{'field':<36} {'P':>6} {'R':>6} {'F1':>6}  tp fp fn wrong")
    all_tp = all_fp = all_fn = all_wrong = 0
    field_f1 = {}
    for f in KLEISTER_FIELDS:
        cnt = agg[f]
        for lbl in ("tp", "fp", "fn", "wrong"):
            cnt.setdefault(lbl, 0)
        p, r, f1 = prf(cnt)
        all_tp += cnt["tp"]
        all_fp += cnt["fp"]
        all_fn += cnt["fn"]
        all_wrong += cnt["wrong"]
        field_f1[f] = round(f1, 3)
        lines.append(f"{f:<36} {p:6.2f} {r:6.2f} {f1:6.2f}  {cnt['tp']:>2} {cnt['fp']:>2} {cnt['fn']:>2} {cnt['wrong']:>3}")

    micro_p, micro_r, micro_f1 = prf({"tp": all_tp, "fp": all_fp, "fn": all_fn})
    macro_f1 = round(sum(field_f1.values()) / len(field_f1), 3)
    lines.append("-" * 70)
    lines.append(f"{'MICRO/FIELD-LEVEL':<36} {micro_p:6.2f} {micro_r:6.2f} {micro_f1:6.2f}  ({all_tp} tp / {all_fp} fp / {all_fn} fn / {all_wrong} wrong)")
    lines.append(f"{'MACRO (field avg)':<36} {micro_p:6.2f} {micro_r:6.2f} {macro_f1:6.2f}  (mean field F1 over {len(KLEISTER_FIELDS)} fields)")

    if n_total:
        lines.append(f"charity_name exact: {total_ok}/{n_total} ({total_ok/n_total:.2f})")

    abstain_by_field = defaultdict(int)
    for d in per_doc:
        for f, res in d["fields"].items():
            if res == "fn":
                abstain_by_field[f] += 1
    if abstain_by_field:
        lines.append("Abstentions: " + ", ".join(f"{k}={v}" for k, v in sorted(abstain_by_field.items())))

    if latencies:
        latencies.sort()
        n = len(latencies)
        lines.append(f"Latency: mean={sum(latencies)/n:.1f}s median={latencies[n//2]:.1f}s min={latencies[0]:.1f}s max={latencies[-1]:.1f}s")
    lines.append(f"Tokens: {cost_total['prompt_tokens']} in / {cost_total['completion_tokens']} out | cost: ${cost_total['cost_usd']:.6f}")

    out = "\n".join(lines)
    print(out)

    scorecard = {
        "split": "kleister", "docs": len(per_doc), "macro_f1": macro_f1,
        "field_f1": field_f1, "charity_name_exact": [total_ok, n_total],
        "latency": {"mean": round(sum(latencies)/len(latencies), 1),
                    "median": round(latencies[len(latencies)//2], 1),
                    "min": round(latencies[0], 1), "max": round(latencies[-1], 1)} if latencies else None,
        "cost_usd": round(cost_total["cost_usd"], 6),
        "tokens": {"prompt": cost_total["prompt_tokens"], "completion": cost_total["completion_tokens"]},
        "abstentions": dict(abstain_by_field),
    }
    with open(os.path.join(RESULTS_DIR, "scorecard_kleister.json"), "w") as f:
        json.dump(scorecard, f, indent=2)
    with open(os.path.join(RESULTS_DIR, "eval_kleister.txt"), "w") as f:
        f.write(out)
    with open(os.path.join(RESULTS_DIR, "per_doc_kleister.json"), "w") as f:
        json.dump(per_doc, f, indent=2)
    return scorecard


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--pred-dir", default="data/predictions")
    ap.add_argument("--gt-format", default="cord", choices=["cord", "self", "kleister"])
    args = ap.parse_args()

    if args.gt_format == "kleister":
        run_kleister(args.manifest, args.pred_dir)
    else:
        run(args.manifest, args.pred_dir, args.gt_format)
