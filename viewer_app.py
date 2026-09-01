"""TEMP viewer app (NOT for git): image | model prediction | ground truth.

Run:  python viewer_app.py   ->  http://localhost:8100
Data: CORD (data/cord_pool) + self-scan (data/self_scan), preds from data/predictions.
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from eval_harness import gt_cord_to_schema, gt_self_to_schema, compare_scalar, SCALAR_FIELDS, PAY_FIELDS, ITEM_FIELDS, compare_items

app = FastAPI(title="Receipt Viewer (TEMP)")


def load_manifest():
    docs = []
    cord = json.load(open(os.path.join(BASE, "data", "cord_pool", "manifest_test.json")))
    for e in cord:
        docs.append({"name": f"cord_{e['idx']:03d}", "split": "CORD",
                     "image": os.path.join(BASE, "data", "cord_pool", e["image"]),
                     "img_rel": f"/img/cord_pool/{e['image']}",
                     "gt_path": os.path.join(BASE, "data", "cord_pool",
                                             os.path.splitext(e["image"])[0] + ".json"),
                     "gt_format": "cord", "idx": e["idx"]})
    selfs = json.load(open(os.path.join(BASE, "data", "self_scan", "manifest.json")))
    for e in selfs:
        docs.append({"name": e["idx"], "split": "SELF",
                     "image": os.path.join(BASE, "data", "self_scan", e["image"]),
                     "img_rel": f"/img/self_scan/{e['image']}",
                     "gt_path": os.path.join(BASE, "data", "self_scan",
                                             os.path.splitext(e["image"])[0] + ".json"),
                     "gt_format": "self", "idx": e["idx"]})
    return docs


DOCS = load_manifest()


def gt_to_schema(doc):
    raw = json.load(open(doc["gt_path"]))
    return gt_cord_to_schema(raw) if doc["gt_format"] == "cord" else gt_self_to_schema(raw)


def pred_for(doc):
    pp = os.path.join(BASE, "data", "predictions", f"pred_{doc['name']}.json")
    if not os.path.exists(pp):
        return None
    pr = json.load(open(pp))
    return pr.get("prediction") or {}


def field_compare(doc, pred, gt):
    """Per-field status for display."""
    rows = []
    for f in SCALAR_FIELDS:
        rows.append({"field": f, "pred": pred.get(f), "gt": gt.get(f),
                     "status": compare_scalar(pred.get(f), gt.get(f))})
    pp, gp = pred.get("payment") or {}, gt.get("payment") or {}
    for f in PAY_FIELDS:
        rows.append({"field": f"payment.{f}", "pred": pp.get(f), "gt": gp.get(f),
                     "status": compare_scalar(pp.get(f), gp.get(f))})
    stats, n_gt, n_pred, name_tp, missing = compare_items(
        pred.get("menu_items") or [], gt.get("menu_items") or [])
    return rows, stats, n_gt, n_pred, name_tp, missing


@app.get("/", response_class=HTMLResponse)
def index():
    html = open(os.path.join(BASE, "viewer_app.html")).read()
    return html


@app.get("/api/docs")
def api_docs():
    return [{"name": d["name"], "split": d["split"], "idx": d["idx"],
             "img": d["img_rel"]} for d in DOCS]


@app.get("/api/doc/{name}")
def api_doc(name: str):
    doc = next((d for d in DOCS if d["name"] == name), None)
    if not doc:
        return {"error": "not found"}
    pred = pred_for(doc)
    gt = gt_to_schema(doc)
    rows, stats, n_gt, n_pred, name_tp, missing = field_compare(doc, pred or {}, gt)
    return {
        "name": doc["name"], "split": doc["split"], "img": doc["img_rel"],
        "prediction": pred, "gt": gt, "fields": rows, "item_stats": stats,
        "items": {"gt": n_gt, "pred": n_pred, "name_tp": name_tp, "missing": missing},
        "has_pred": pred is not None,
    }


app.mount("/img", StaticFiles(directory=os.path.join(BASE, "data")), name="img")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100)
