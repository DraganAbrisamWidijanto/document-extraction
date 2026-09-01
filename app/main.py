"""FastAPI app: receipt extraction API.

POST /extract  — upload image, get structured JSON + confidence + cost
GET  /health   — liveness check
"""
import base64
import os
import sys
import uuid
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from pydantic import BaseModel

# Add parent to path for local dev
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.extractor import Config, extract_receipt

app = FastAPI(title="Receipt Extractor", version="0.3.0", docs_url="/docs")
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
async def extract(file: Optional[UploadFile] = File(None),
                  image: Optional[str] = Form(None)):
    """
    Upload a receipt image (multipart file) or provide base64 image string.
    Returns structured extraction JSON with per-field confidence and cost.
    """
    if file:
        img_bytes = await file.read()
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
