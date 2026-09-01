# Receipt Document Extraction — Datasaur Take-Home

VLM-based receipt extraction with a first-class eval harness. Extracts menu items, subtotal/discount/tax/service/total, and payment info from receipt photos (CORD receipts + real-world Indonesian receipts).

## Architecture

```
client ──POST /extract──> FastAPI (app/main.py)
                              │
                              ▼
                      VLM (app/extractor.py)
                      OpenAI-compatible endpoint
                      (default: bai/mimo-v2.5 via local 9Router)
                              │
                              ▼
                    structured JSON (schema below)
```

Two run modes:
- **API**: `uvicorn app.main:app` — extract one image per request.
- **Eval**: `eval_harness.py` — batch field-level metrics on the test set.

## Schema

```json
{
  "menu_items": [{"name", "quantity", "unit_price", "amount"}],
  "subtotal": 0, "discount": null, "tax": 0, "service_charge": null, "total": 0,
  "payment": {"method": "cash|credit_card|emoney|...", "amount_paid": 0, "change": null}
}
```
Numbers are plain integers (no separators/currency). Absent fields are `null` — the model is instructed to abstain rather than guess.

## Dataset

- **CORD** (100 test receipts, 2.3 GB source) — open benchmark. We selected **50 diverse** (greedy max-min on features: item count, tax/discount/service presence, payment type, image area) → **5 dev + 45 test**.
- **5 self-scans** — real Indonesian receipts (NASGOR 69, Ayam Gepuk Pak Gembus, Ayam Mertua UGM, Mie Bonafide, Rokaville) with hand-verified GT (incl. vendor/date — fields CORD lacks).

Layout under `data/`:
```
dev_sample/   30 CORD sampled (seed 42) + GT (gt_parse format)
cord_pool/    50 diverse CORD: manifest_dev.json / manifest_test.json + GT + PNGs
self_scan/    5 self-scans: manifest.json + GT (schema format) + JPGs
predictions/  batch extraction output (pred_cord_XXX.json, pred_self_XXX.json)
```

## Running

### API
```bash
# env: VLM_BASE_URL, VLM_API_KEY, VLM_MODEL (see app/extractor.py for defaults)
uvicorn app.main:app --port 8000
curl -F "file=@receipt.png" http://localhost:8000/extract
```

### Docker
```bash
docker compose build
docker compose up api
docker compose run --rm eval
```

### Eval
```bash
python eval_harness.py --manifest data/cord_pool/manifest_test.json --pred-dir data/predictions --gt-format cord
python eval_harness.py --manifest data/self_scan/manifest.json    --pred-dir data/predictions --gt-format self
```
Both GT formats are supported: CORD `gt_parse` and self-scan schema.

## Metrics

Field-level P/R/F1 over: subtotal, discount, tax, service_charge, total, payment.method, payment.amount_paid, payment.change, item.quantity, item.unit_price, item.amount. Error typology: **tp / fp / fn / wrong** (fp = predicted but absent in GT; fn = GT present but model abstained; wrong = both present, value differs). Plus item-name match P/R/F1, total-exact rate, abstention count, and latency (mean/median/min/max). Full summary under `results/eval_{cord,self}.txt`.

### Dev-loop findings (prompt iterations)
| Issue | Before | After prompt v2 |
|---|---|---|
| unit_price abstained (075) | null ×6 | filled (29091…) |
| service digit error (014) | 17,050 | 17,850 ✓ |
| unit_price = line total (000) | 60,000 | still 60,000 (edge: receipt prints no unit price) |

### Final metrics (45 CORD + 5 self-scan)
| Split | Macro F1 | total exact | Item name F1 | Latency (mean/median) |
|---|---|---|---|---|
| CORD (45) | **0.84** | **41/45 (91%)** | 0.89 | 94s / 41s |
| Self (5)  | **0.96** | **5/5 (100%)** | 0.95 | 28s / 23s |

### Optimization: image downscaling
7 images failed initially (timeout, 5.7–16.8 MB, 2048×4096 px). **Downscaled to max 1600px + JPEG 85** (200–400KB, 2–11% of original) → **all 7 succeeded**. Impact: +13% total accuracy, −35% mean latency, zero timeouts. (`retry_failed.py` — note: never import without `__main__` guard; the module‑level loop will write into your prediction files).

## Notes / decisions

- VLM: `bai/mimo-v2.5` via 9Router (stream=False, data-URI image). Fallback `bai/deepseek-v4-flash-vision-exp`.
- Retry policy: 429/402/403 backoff (30s×attempt), timeout 180s/req (large 2304×4096 photos often need the retry).
- Optimizations (measured in eval summary): retry-with-backoff keeps success at ~100% despite timeouts; greedy diversity selection keeps test size small (50) → low API cost.
- 9Router API key is read from its local SQLite (`~/AppData/Roaming/9router/db/data.sqlite`) to avoid masking `sk-*` in shell history; in Docker set `VLM_API_KEY`.
