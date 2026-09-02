# Receipt Extraction — Datasaur Take-Home

Extracts structured fields from receipt photos (items, totals, payment) and multi-page charity PDFs (8 fields: charity, address, income/spending, date) with a VLM, and proves accuracy with a field-level eval harness.

## Quick start

```bash
docker compose build
docker compose up api          # API on :8000
docker compose run --rm eval   # end-to-end eval: re-extracts all 67 docs through the API, writes scorecards to results/
```

API key: put your key(s) in `geminiapi.txt` at the repo root (one per line) or set `VLM_API_KEY` in `.env` (copy `.env.example`).

Test the API directly:

```bash
curl -F "file=@data/self_scan/self_001.jpg" http://localhost:8000/extract       # receipt image
curl -F "file=@data/kleister/documents/af94fcab824a6df19c6954841adf44be.pdf" \
     http://localhost:8000/extract-pdf                                          # multi-page PDF
```

Without Docker:

```bash
uvicorn app.main:app --port 8000                             # API
python run_eval.py --live                                    # end-to-end eval, in-process (all 3 splits)
python run_eval.py --split kleister                          # just the multi-page split
python compare_evals.py --base <old>.json --new <new>.json   # A/B regression gate (exit 1 on regression)
```

## Approach

A single VLM call per receipt (Gemini 3.5 Flash-Lite, any OpenAI-compatible endpoint works via env vars). A receipt is one page the model reads in one pass — reasoning like "which Total line is final", null vs 0, and tax-inclusive math lives in explicit prompt rules instead of an agent loop. Output is parsed, validated against the schema by a hand-written validator, and returned with per-field confidence.

For multi-page PDFs (Kleister), the pipeline in `app/pdf_extractor.py` is:

1. Extract text per page locally with PyMuPDF (free, no API calls).
2. Score pages for relevant signal (charity number, address/postcode, income/expenditure tables, report date) and select the top ~7 pages — a 100-page report costs ~7 VLM calls, not 100.
3. Render the selected pages and run the VLM once per page, passing the page's text layer to verify dense financial tables (image stays the source of truth).
4. Merge per-page results field-wise: highest-confidence value wins; conflicting values lower confidence; absent fields abstain.

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
- **Kleister-Charity** (open benchmark) — 12 multi-page UK charity annual reports (39–122 pages each), 8 fields (charity name/number, registered address ×3, annual income/spending, report date). PDFs + official GT (from the dataset's `expected.tsv`) committed under `data/kleister/`. This is the multi-page stress test: the pipeline must locate and merge fields spread across dozens of pages.

## Results

Official run: `docker compose run --rm eval` — all 67 documents re-extracted live through the containerized API (CORD and self-scan from the containerized run; Kleister numbers below from the in-process pipeline after the containerized run surfaced and fixed an async-endpoint bottleneck plus two prompt/selection bugs — the fixes were verified locally on the same 12 documents). Scorecards and per-document results committed in `results/`.

| Split | Macro F1 | Total exact | Item-name F1 | Latency mean/median | Cost |
|---|---|---|---|---|---|
| CORD (45) | 0.93 | 44/45 | 0.93 | 33.1s / 33.0s | $0.00 |
| Self-scan (10) | 0.99 | 10/10 | 1.00 | 33.0s / 33.0s | $0.00 |
| Kleister (12) | 0.95 | — | — | 252.9s / 240.0s | $0.00 |

- Metrics: per-field P/R/F1 with failure kinds (tp / fp / fn / wrong), abstention counts, latency, tokens, cost. Macro F1 averages only fields that have GT instances, so it stays comparable across versions. Kleister uses 8 fields; the comparator normalizes Kleister's official GT format (legal-form suffixes like Ltd/Limited, one-sided abbreviations like 'Citb', street ranges like '2-3' vs '2') before comparing; 4 of the remaining street-line `fp`s are cases where the GT deliberately records no street line but the report prints one — counted as errors per the official convention, documented rather than patched.
- Kleister error kinds after the fixes: 2 report-date misses (audit date vs period end) and 3 rounded income/spending figures were eliminated by prompt rules (period-end date; never round, abstain instead); 5 SoFA-page abstentions were eliminated by adding a totals-page boost to page selection. Remaining: 3 rounded-figure `wrong`s where the report itself only prints approximations, 2 postcode `wrong`s, and the 4 GT-null street `fp`s above.
- The one CORD miss (cord_095): the receipt prints cash paid (62.000) right next to total due (61.799) and the model picked the cash amount. A known ambiguous case, documented rather than patched around.
- One optimization with measured impact: oversized images are downscaled (1600px JPEG) before the call — recovered 7/7 timeout failures, −35% mean latency, +13 points total-exact.

## Agentic development

Built with Hermes Agent (Nous Research): it analyzed the requirements PDF, explored CORD and selected a diverse pool, tested and picked the VLM, iterated the prompt (v1→v7) driven by eval runs after each change, wrote the eval harness, and audited the final code against the PDF.

Custom extension (used every run): `KeyPool` in `app/extractor.py` — thread-safe round-robin over 5 free-tier Gemini keys with automatic failover on 429/5xx/timeouts. This is what makes 5-worker concurrent extraction viable on free-tier rate limits.

## Trade-offs

| Decision | Why |
|---|---|
| Single VLM call per page, merged field-wise | Multi-page PDFs handled without agent loop; ~7 calls per 100-page report |
| Page selection via text-layer scoring | Avoids running VLM on every page; cheap, local, deterministic; totals-page boost guarantees the SoFA income/spending page is selected |
| Text-layer passed to VLM for financial tables | Fixes column-confusion in dense SoFA tables (current-year vs prior-year comparative) |
| Sync (`def`) endpoints | The VLM call takes 30s–4min; sync endpoints run in FastAPI's threadpool so concurrent extractions are actually parallel (an `async def` endpoint blocks the event loop and serializes them) |
| One shared compose image tag (`image: datasaur:latest`) | `compose run` services otherwise build their own project-scoped image that silently goes stale — the bug that made the first containerized eval run old code |
| Gemini free tier, 5 keys round-robin | $0.00 cost; key file easy for the reviewer to swap |
| Own validator instead of pydantic | Requirement says extraction logic should be your own |
| Eval artifacts committed in repo | Reviewer sees real run output, not just claims |

## What I would do with more time

Confidence calibration (turn per-field confidence into a usable trust threshold), vendor/date eval on all CORD documents (its GT lacks them), and a CI gate that runs `compare_evals.py` on every change.
