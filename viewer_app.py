"""TEMP viewer app (NOT for git): image | model prediction | ground truth.

Run:  python viewer_app.py   ->  http://localhost:8100
Data: CORD (data/cord_pool) + self-scan (data/self_scan) + Kleister (data/kleister).
"""
import json
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from eval_harness import (gt_cord_to_schema, gt_self_to_schema, gt_kleister_to_schema,
                          compare_scalar, SCALAR_FIELDS, PAY_FIELDS, ITEM_FIELDS,
                          KLEISTER_FIELDS, compare_items)

app = FastAPI(title="Receipt & Document Viewer (TEMP)")

# Pre-render first page of Kleister PDFs for preview
KLEISTER_PREVIEW_DIR = os.path.join(BASE, "data", "kleister", "preview")
os.makedirs(KLEISTER_PREVIEW_DIR, exist_ok=True)


def _render_pages(pdf_path: str, page_indices, out_dir: str, stem: str):
    """Render each selected page as PNG for the viewer. Returns list of rel paths."""
    paths = []
    try:
        import pymupdf
        doc = pymupdf.open(pdf_path)
        for i in page_indices:
            if i >= len(doc):
                continue
            out = os.path.join(out_dir, f"{stem}_p{i}.png")
            if not os.path.exists(out):
                pix = doc[i].get_pixmap(dpi=120)
                pix.save(out)
            paths.append(f"/img/kleister/preview/{stem}_p{i}.png")
        doc.close()
    except Exception:
        pass
    return paths


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
    kleister = json.load(open(os.path.join(BASE, "data", "kleister", "manifest.json")))
    for e in kleister:
        stem = e["idx"]
        pdf_path = os.path.join(BASE, "data", "kleister", "documents", e["image"])
        # pages actually used by the pipeline (from prediction file), else page 0
        pr_path = os.path.join(BASE, "data", "predictions", f"pred_{stem}.json")
        pages = []
        if os.path.exists(pr_path):
            pr = json.load(open(pr_path))
            pages = pr.get("page_indices") or []
        if not pages:
            pages = [0]
        rel = _render_pages(pdf_path, pages, KLEISTER_PREVIEW_DIR, stem)
        docs.append({"name": stem, "split": "KLEISTER",
                     "image": pdf_path,
                     "img_rel": rel[0] if len(rel) == 1 else rel,
                     "img_pages": rel,
                     "gt_path": os.path.join(BASE, "data", "kleister", f"{stem}.json"),
                     "gt_format": "kleister", "idx": stem})
    return docs


DOCS = load_manifest()


def gt_to_schema(doc):
    raw = json.load(open(doc["gt_path"]))
    if doc["gt_format"] == "cord":
        return gt_cord_to_schema(raw)
    elif doc["gt_format"] == "self":
        return gt_self_to_schema(raw)
    else:
        return gt_kleister_to_schema(raw)


def pred_for(doc):
    pp = os.path.join(BASE, "data", "predictions", f"pred_{doc['name']}.json")
    if not os.path.exists(pp):
        return None
    pr = json.load(open(pp))
    return pr.get("prediction") or {}


def field_compare(doc, pred, gt):
    """Per-field status for display. Handles all 3 formats."""
    rows = []
    if doc["gt_format"] == "kleister":
        for f in KLEISTER_FIELDS:
            rows.append({"field": f, "pred": pred.get(f), "gt": gt.get(f),
                         "status": compare_scalar(pred.get(f), gt.get(f))})
        return rows, {}, 0, 0, 0, []
    # receipt format
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
    result = {
        "name": doc["name"], "split": doc["split"], "img": doc["img_rel"],
        "img_pages": doc.get("img_pages", []),
        "prediction": pred, "gt": gt, "fields": rows, "item_stats": stats,
        "items": {"gt": n_gt, "pred": n_pred, "name_tp": name_tp, "missing": missing},
        "has_pred": pred is not None,
    }
    # Attach extraction metadata from prediction file (Kleister)
    pr_path = os.path.join(BASE, "data", "predictions", f"pred_{doc['name']}.json")
    if os.path.exists(pr_path):
        pr = json.load(open(pr_path))
        result["latency_s"] = pr.get("latency_s")
        result["usage"] = pr.get("usage")
        result["pages_total"] = pr.get("pages_total")
        result["pages_selected"] = pr.get("pages_selected")
        result["page_indices"] = pr.get("page_indices")
        # per-field page provenance (Kleister)
        page_results = pr.get("page_results") or []
        if page_results and doc["gt_format"] == "kleister":
            from collections import defaultdict
            field_pages = defaultdict(list)
            for pr_page in page_results:
                p = pr_page.get("prediction") or {}
                for f in KLEISTER_FIELDS:
                    if p.get(f) is not None:
                        field_pages[f].append(pr_page.get("page"))
            result["field_pages"] = {f: sorted(v) for f, v in field_pages.items()}
    return result


app.mount("/img/cord_pool", StaticFiles(directory=os.path.join(BASE, "data", "cord_pool")), name="cord_pool")
app.mount("/img/self_scan", StaticFiles(directory=os.path.join(BASE, "data", "self_scan")), name="self_scan")
app.mount("/img/kleister/preview", StaticFiles(directory=KLEISTER_PREVIEW_DIR), name="kleister_preview")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8100)
