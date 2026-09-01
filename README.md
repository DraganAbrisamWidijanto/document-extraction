# Receipt Extraction — Datasaur Take-Home

Extracts structured fields (items, totals, payment) from receipt photos with a VLM, and proves accuracy with a field-level eval harness.

## Quick start

```bash
docker compose build
docker compose up api          # API on :8000
docker compose run --rm eval   # end-to-end eval: re-extracts all 55 docs through the API, writes scorecards to results/
```

API key: put your key(s) in `geminiapi.txt` at the repo root (one per line) or set `VLM_API_KEY` in `.env` (copy `.env.example`).

Test the API directly:

```bash
curl -F "file=@data/self_scan/self_001.jpg" http://localhost:8000/extract
```

Without Docker:

```bash
uvicorn app.main:app --port 8000                             # API
python run_eval.py --live                                    # end-to-end eval, in-process
python compare_evals.py --base <old>.json --new <new>.json   # A/B regression gate (exit 1 on regression)
```

## Approach

A single VLM call per receipt (Gemini 3.5 Flash-Lite, any OpenAI-compatible endpoint works via env vars). A receipt is one page the model reads in one pass — reasoning like "which Total line is final", null vs 0, and tax-inclusive math lives in explicit prompt rules instead of an agent loop. Output is parsed, validated against the schema by a hand-written validator, and returned with per-field confidence.

- Absent field → `null` with confidence 0.0 (abstain, never guess). Explicitly printed zero → `0`.
- Confidence comes from the model and is not yet calibrated (it's similar for right and wrong fields), so measured abstention is the honest "knows when unsure" signal.

```json
{ "menu_items": [ { "name": "...", "quantity": 1, "unit_price": 10000, "amount": 10000,
                    "confidence": { "name": 0.95, "quantity": 0.95, "unit_price": 0.95, "amount": 0.95 } } ],
  "subtotal": 50000, "discount": null, "tax": 5000, "service_charge": null, "total": 55000,
  "confidence": { "subtotal": 0.98, "discount": 0.0, "total": 0.99 },
  "payment": { "method": "cash", "amount_paid": 55000, "change": null } }
```

## Dataset

- **CORD** (open benchmark) — 100 test receipts, picked 50 for diversity (item count, tax/discount/service, payment type) → 5 dev + 45 test. Images + GT committed under `data/`.
- **10 self-scans** — real Indonesian receipts, GT hand-verified against each photo (recorded in each GT's `meta.note`). Covers vendor/date fields CORD lacks.

## Results

Official run: `docker compose run --rm eval` — all 55 documents re-extracted live through the containerized API. Scorecards and per-document results committed in `results/`.

| Split | Macro F1 | Total exact | Item-name F1 | Latency mean/median | Cost |
|---|---|---|---|---|---|
| CORD (45) | 0.93 | 44/45 | 0.93 | 3.7s / 2.6s | $0.00 |
| Self-scan (10) | 0.99 | 10/10 | 1.00 | 5.4s / 2.5s | $0.00 |

- Metrics: per-field P/R/F1 with failure kinds (tp / fp / fn / wrong), abstention counts, latency, tokens, cost. Macro F1 averages only fields that have GT instances, so it stays comparable across versions.
- The one CORD miss (cord_095): the receipt prints cash paid (62.000) right next to total due (61.799) and the model picked the cash amount. A known ambiguous case, documented rather than patched around.
- One optimization with measured impact: oversized images are downscaled (1600px JPEG) before the call — recovered 7/7 timeout failures, −35% mean latency, +13 points total-exact.

## Agentic development

Built with Hermes Agent (Nous Research): it analyzed the requirements PDF, explored CORD and selected a diverse pool, tested and picked the VLM, iterated the prompt (v1→v7) driven by eval runs after each change, wrote the eval harness, and audited the final code against the PDF.

Custom extension (used every run): `KeyPool` in `app/extractor.py` — thread-safe round-robin over 5 free-tier Gemini keys with automatic failover on 429/5xx/timeouts. This is what makes 5-worker concurrent extraction viable on free-tier rate limits.

## Trade-offs

| Decision | Why |
|---|---|
| Single VLM call, no agent loop | One-page docs; lower latency and cost; simple failure mode |
| Gemini free tier, 5 keys round-robin | $0.00 cost; key file easy for the reviewer to swap |
| Own validator instead of pydantic | Requirement says extraction logic should be your own |
| Eval artifacts committed in repo | Reviewer sees real run output, not just claims |

## What I would do with more time

Multi-page documents, vendor/date eval on all documents (CORD's GT lacks them), confidence calibration, and a CI gate that runs `compare_evals.py` on every change.
