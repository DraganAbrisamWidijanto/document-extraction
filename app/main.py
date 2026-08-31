"""FastAPI app: receipt extraction API.

POST /extract  — upload image, get structured JSON (handles base64 JSON or multipart file)
GET  /health  — liveness check
"""
import os, sys, uuid, json, base64
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from typing import Optional

# Add parent to path for local dev
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.extractor import extract_receipt, api_config

app = FastAPI(title="Receipt Extractor", version="0.2.0", docs_url="/docs")
cfg = api_config()


class ExtractResult(BaseModel):
    id: str
    latency_s: float
    total: Optional[float]
    items: int
    raw_preview: str


@app.get("/health")
def health():
    return {"status": "ok", "model": cfg["model"], "base_url": cfg["base_url"]}


@app.post("/extract")
async def extract(file: Optional[UploadFile] = File(None),
                  image: Optional[str] = Form(None)):
    """
    Upload a receipt image (multipart file) or provide base64 image string.
    Returns structured extraction JSON.
    """
    if file:
        img_bytes = await file.read()
        mime = file.content_type or "image/png"
    elif image:
        # data URI or raw base64
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
        "total": pred.get("total"),
        "items": len(pred.get("menu_items") or []),
        "prediction": pred,
        "raw": result["raw"],
    }