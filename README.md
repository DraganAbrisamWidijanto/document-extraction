# Receipt Document Extraction — Datasaur Take-Home

VLM-based receipt extraction with a first-class eval harness. Extracts menu items, subtotal/discount/tax/service/total, and payment info from receipt photos (CORD receipts + real-world Indonesian receipts).

## Architecture

```
client ──POST /extract──> FastAPI (app/main.py)
                             │
                             ▼
                     VLM (app/extractor.py)
                     OpenAI-compatible endpoint
                     (default: Gemini 3.5 Flash-Lite via free tier)
                             │
                             ▼
                   structured JSON (schema v3 with confidence)
```

Two run modes:
- **API**: `uvicorn app.main:app` — extract one image per request.
- **Eval**: `eval_harness.py` — batch field-level metrics; `run_eval.py --live` — end-to-end.

**Why a single extraction call, not an agentic pipeline:** a receipt is one page a VLM can read in a single pass — there is no multi-hop lookup, tool use, or cross-document state that would benefit from an agent loop. A single well-designed call keeps latency and cost minimal and the failure mode simple: one request, one JSON response, then validation. The reasoning burden (which "Total" line is final, null vs 0, tax-inclusive arithmetic) is pushed into explicit prompt rules instead of extra agentic steps. An agentic pipeline would only pay off for multi-page documents or cross-document reconciliation (see "What I Would Do With More Time").

## Schema (v3)

```json
{
  "menu_items": [
    {"name": "...", "quantity": 1, "unit_price": 10000, "amount": 10000,
     "confidence": {"name": 0.99, "quantity": 0.98, "unit_price": 0.9, "amount": 0.98}}
  ],
  "subtotal": 50000, "discount": null, "tax": 5000, "service_charge": null, "total": 55000,
  "confidence": {"subtotal": 0.98, "discount": 0.0, "tax": 0.97, "service_charge": 0.0,
                 "total": 0.99, "payment.method": 0.98, "payment.amount_paid": 0.97, "payment.change": 0.0},
  "payment": {"method": "cash", "amount_paid": 55000, "change": 0}
}
```

- Numbers are plain integers. Absent fields are `null` (abstained, confidence 0.0).
- **Per-field confidence** (0.0–1.0) — model reports how sure it is. Abstentions have confidence 0.0.
- Output is schema-validated (`validate_prediction`): typed, enums checked, ranges checked.

A note on confidence honesty: on the current run, mean self-reported confidence is nearly identical for correct and wrong fields (e.g. total 0.94 vs 0.95 on CORD) — the model's self-report is **not yet calibrated**, so treat it as a relative signal only. The reliable "knows when unsure" signal is measured **abstention** (null + confidence 0.0 when the field is absent/illegible), which the harness counts per field.

## Dataset

- **CORD** (100 test receipts, 2.3 GB source) — open benchmark. We selected **50 diverse** (greedy max-min on features: item count, tax/discount/service presence, payment type, image area) → **5 dev + 45 test**.
- **10 self-scans** — real Indonesian receipts (NASGOR 69, Ayam Gepuk Pak Gembus, Ayam Mertua UGM, Mie Bonafide, ROKAVILLE, Olive Fried Chicken, Tottori_ID, Relasi Space After Home, SARSA Creative Space, Maverick) with GT incl. vendor/date (fields CORD lacks). First 5 hand-built from the photos; last 5 VLM-drafted then each verified by hand against the photo (vendor, date, items, amounts; explicit-vs-derived, tax-inclusive, EDC→null) — verification recorded in each GT's `meta.note`.

Layout under `data/`:
```
dev_sample/   30 CORD sampled (seed 42) + GT (gt_parse format)
cord_pool/    50 diverse CORD: manifest_dev.json / manifest_test.json + GT + PNGs
self_scan/    10 self-scans: manifest.json + GT (schema format) + JPGs
predictions/  batch extraction output (pred_cord_XXX.json, pred_self_XXX.json)
```

## Running

### API
```bash
# env: VLM_BASE_URL, VLM_API_KEY or geminiapi.txt, VLM_MODEL (see app/extractor.py)
uvicorn app.main:app --port 8000
curl -F "file=@receipt.png" http://localhost:8000/extract
```

### Docker
```bash
docker compose build
docker compose up api          # extraction API on :8000
docker compose run --rm eval   # end-to-end: extract fresh THROUGH the API, then score
```
The eval service POSTs every image to the running API's `/extract` endpoint (`run_eval.py --live --api-url http://api:8000`), so the harness is exercised end-to-end against the deployed API — not just the library in-process. Locally, `python run_eval.py --live` without `--api-url` uses the in-process pipeline (same code path, no HTTP hop).

### Eval (existing predictions)
```bash
python eval_harness.py --manifest data/cord_pool/manifest_test.json --pred-dir data/predictions --gt-format cord
python eval_harness.py --manifest data/self_scan/manifest.json    --pred-dir data/predictions --gt-format self
```
Both GT formats are supported: CORD `gt_parse` and self-scan schema.

### Batch extraction (concurrent)
```bash
python batch_extract.py --source all --workers 5   # 5 concurrent workers (1 per key)
```

### A/B regression gate
```bash
python compare_evals.py --base results/scorecard_cord_v1.json --new results/scorecard_cord_v2.json --max-drop 0.02
```

## Metrics

Field-level P/R/F1 over: subtotal, discount, tax, service_charge, total, payment.method, payment.amount_paid, payment.change, item.quantity, item.unit_price, item.amount. Error typology: **tp / fp / fn / wrong** (fp = predicted but absent in GT; fn = GT present but model abstained; wrong = both present, value differs). Plus **item-name match** P/R/F1, **total-exact rate**, **abstention count per field**, **mean confidence per field for tp vs wrong**, **latency** (mean/median/min/max), **token usage** and **cost** (USD). Scorecard output as JSON for A/B comparison. **Macro F1** = mean per-field F1 over *evaluable* fields only (fields with at least one GT instance; the set is determined by the dataset, so it is stable across versions) — fields with no GT instances are excluded and named in the summary.

### Final metrics (45 CORD + 10 self-scan) — Gemini 3.5 Flash-Lite (free tier)

| Split | Macro F1 | total exact | Item name F1 | Latency (mean/median) | Cost |
|---|---|---|---|---|---|
| CORD (45) | **0.93** | **44/45 (98%)** | 0.92 | 14.0s / 3.4s | $0.00 |
| Self (10) | **0.99** | **10/10 (100%)** | **1.00** | 2.6s / 2.6s | $0.00 |

*(Macro F1: CORD 0.928 over 11 evaluable fields; self 0.986 over 9 — `discount` and `service_charge` excluded because no self-scan receipt carries those lines. Latest run, Sep 2 2026 — prompt v7: explicit-vs-derived null/0, tax-inclusive handling, EDC→null, integer-only money, bundle/DISC line rules, final-total = amount-paid rule, modifier lines not separate items. Item fields on self are 1.00/1.00/1.00 (37/37 items); the only CORD total miss is cord_095 — a flaky ambiguous receipt where the model reads the cash amount (62,000) as the total instead of total due (62,000 − 201 = 61,799).)*

### Optimization: image downscaling
7 images failed initially (timeout, 5.7–16.8 MB, 2048×4096 px). **Downscaled to max 1600px + JPEG 85** (200–400KB, 2–11% of original) → **all 7 succeeded**. Impact: +13% total accuracy, −35% mean latency, zero timeouts. Now integrated into the main pipeline (`extractor.downscale`), not just retry.

### Key swap on failure
Each 429/5xx rotates to the next free-tier key (5 keys round-robin) instead of sleeping 30s — eliminates backoff wait for the same key pool.

## Agentic Development

This project was built using **Hermes Agent** (Nous Research) — an AI coding agent that operates in a live terminal, reading/writing files, running tests, and iterating on code. The development process was:

1. **Requirements analysis** — agent read the take-home PDF, extracted criteria, and built a checklist.
2. **Dataset exploration** — CORD parquet analysed, pool of 50 diverse receipts selected via greedy max-min.
3. **VLM selection** — 31 models tested via 9Router; `mimo-v2.5` was the only working VLM initially. Later migrated to **Gemini 3.5 Flash-Lite** free tier (5 round-robin keys) for shareability and speed.
4. **Prompt engineering** — 2 iterations: schema v1 → v2 (unit_price/quantity rules, discount sign, payment method enum).
5. **Eval harness** — written from scratch with field-level metrics and error typology; iterated after every dev run.
6. **Optimization** — downscaling measured and integrated; multi-key failover; concurrency.
7. **Code audit** — post-hoc audit against the requirement PDF identified 7 gaps (confidence, cost, validation, A/B comparison, .env.example, E2E eval, agentic note). All fixed.

### Custom extension: Multi-key pool with automatic failover

The `KeyPool` class (`app/extractor.py`) implements a thread-safe round-robin over 5 free-tier Gemini API keys. On 429/5xx/timeout, it skips the failed key and tries the next one — eliminating backoff delays and distributing load evenly. This is essential for concurrent extraction (5 workers, 1 key each) and avoids hitting per-key rate limits. The pool is provider-agnostic: any OpenAI-compatible VLM can use it.

## Decisions & Trade-offs

| Decision | Rationale |
|---|---|
| URL → Gemini OpenAI-compatible | Minimal code change; same `/chat/completions` shape as any OpenAI-compatible provider |
| Free tier (5 keys, round-robin) | Zero cost, 1500 RPD per key, shareable with Datasaur engineer |
| Downscaling in main pipeline | Image > 1 MB triggers JPEG resize 1600px — prevents timeouts without separate retry step |
| Own validator (not pydantic) | Matches requirement "extraction logic should be your own"; no extra dependency |
| Confidence per field | Explicit requirement; also used for abstention (confidence 0.0 = field absent) |
| JSON scorecard | Enables A/B comparison and regression gating (`compare_evals.py`) |
| ThreadPoolExecutor (not asyncio) | Stdlib, simple, no new deps; Gemini API is synchronous anyway |

## What I Would Do With More Time

- **Multi-page support** — stitch page images or use a model with longer context.
- **Per-field confidence calibration** — measure precision@confidence thresholds.
- **Vendor/date extraction** — CORD doesn't have these; extend self-scan GT and eval to all documents.
- **Streaming / real-time** — WebSocket endpoint for low-latency scanning.
- **CI integration** — GitHub Actions that runs `compare_evals` on every PR to gate regressions.