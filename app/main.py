"""FastAPI app: receipt + PDF extraction API.

POST /extract      — upload image, get structured JSON + confidence + cost
POST /extract-pdf  — upload PDF (multi-page charity report), get Kleister fields
GET  /health       — liveness check
"""
import base64
import os
import sys
import tempfile
import uuid
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from pydantic import BaseModel

# Add parent to path for local dev
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.extractor import Config, extract_receipt
from app.pdf_extractor import extract_kleister_pdf

app = FastAPI(title="Receipt & Document Extractor", version="0.4.0", docs_url="/docs")
cfg = Config.from_env()


class ExtractResult(BaseModel):
    id: str
    latency_s: float
    cost_usd: float
    total: Optional[float]
    items: int
    valid: bool
    raw_preview: str


@app.get("/health")
def health():
    return {"status": "ok", "model": cfg.model, "base_url": cfg.base_url,
            "keys": len(cfg.keys)}


@app.post("/extract")
def extract(file: Optional[UploadFile] = File(None),
            image: Optional[str] = Form(None)):
    """Upload a receipt image (multipart file) or provide base64 image string.
    Returns structured extraction JSON with per-field confidence and cost.

    Sync (def): the VLM call takes ~30s; a sync endpoint runs in FastAPI's
    threadpool instead of blocking the event loop, so concurrent requests
    are processed in parallel.
    """
    if file:
        img_bytes = file.file.read()
        mime = file.content_type or "image/png"
    elif image:
        if image.startswith("data:"):
            mime = image.split(";")[0].split(":")[1]
            img_bytes = base64.b64decode(image.split(",")[1])
        else:
            img_bytes = base64.b64decode(image)
            mime = "image/png"
    else:
        raise HTTPException(400, "Provide 'file' (multipart) or 'image' (base64).")

    result = extract_receipt(img_bytes, mime, cfg)
    pred = result["prediction"]
    req_id = uuid.uuid4().hex[:8]
    return {
        "id": req_id,
        "latency_s": result["latency_s"],
        "cost_usd": result["cost_usd"],
        "usage": result["usage"],
        "total": pred.get("total"),
        "items": len(pred.get("menu_items") or []),
        "validation": result["validation"],
        "prediction": pred,
        "raw": result["raw"],
    }


@app.post("/extract-pdf")
def extract_pdf(file: UploadFile = File(...)):
    """Upload a PDF document (multi-page charity report).
    Returns structured extraction JSON with 8 Kleister fields.

    Sync (def) on purpose: extraction is CPU/IO-heavy and minutes-long. A sync
    endpoint runs in FastAPI's threadpool, so concurrent PDF requests are
    processed in parallel; an async endpoint would block the event loop and
    serialize every request behind the running extraction.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Provide a PDF file.")

    pdf_bytes = file.file.read()
    if len(pdf_bytes) < 20:
        raise HTTPException(400, "Empty or truncated PDF.")
    magic = pdf_bytes[:5]
    if magic != b"%PDF-" and not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Not a PDF file (bad magic bytes and no .pdf extension).")

    # Write to temp then run pipeline
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
    try:
        result = extract_kleister_pdf(tmp_path, cfg)
    finally:
        os.unlink(tmp_path)

    pred = result.get("prediction") or {}
    req_id = uuid.uuid4().hex[:8]
    return {
        "id": req_id,
        "latency_s": result["latency_s"],
        "cost_usd": result["cost_usd"],
        "usage": result.get("usage"),
        "tokens": result.get("tokens"),
        "validation": result.get("validation"),
        "confidence": result.get("confidence"),  # per-field 0-1 (abstained = 0.0)
        "pages_total": result.get("pages_total"),
        "pages_selected": result.get("pages_selected"),
        "page_indices": result.get("page_indices"),
        "prediction": pred,
        "raw": result.get("raw", ""),
    }
