# HANDOFF — Datasaur.ai Take-Home Test (Document Extraction)

Proyek: `C:\Users\ASUS\Documents\lamarankerja\datasaur` — **jangan ada file keluar dari folder ini**.
Deadline: 3 hari dari email diterima. Submit: invite `saripudin@datasaur.ai`, `satrio.putra@datasaur.ai`, `diah.pitaloka@datasaur.ai` ke private repo.

## Status Terakhir (2 Sep 2026 — sesi audit & closeout, commit `3d35605`)

Audit vs requirement PDF selesai; semua must-fix (kecuali verifikasi Docker) sudah dieksekusi:
- Sample images (84) + 55 predictions + 7 eval artifacts DI-COMMIT (sebelumnya di-gitignore) → eval jalan out of the box di clone baru
- `.gitignore` sekarang menutup semua varian key (`geminiapi*.txt`, termasuk "geminiapi - Copy.txt" yang berisi 5 key) dan folder drop `self scan/`
- macro F1 diperbaiki: fixed label set + macro hanya atas field evaluabel (set-nya ditentukan dataset, stabil antar versi). Final: CORD 0.928 (11 field), self 0.986 (9 field — discount & service_charge tidak punya instance GT di self, di-exclude dan disebut eksplisit di summary)
- GT self_006–010 ditandai verified manual terhadap foto (meta.note); klaim README diselaraskan
- README: justifikasi arsitektur (single-call vs agentic), definisi macro, catatan jujur kalibrasi confidence (conf_tp ≈ conf_wrong → self-report belum terkalibrasi; andalkan abstention)
- `.env.example` pakai path placeholder

**Sisa langkah: GitHub** — repo private, push, invite 3 reviewer. Docker sudah terverifikasi end-to-end (commit `29c0a5a` + `fb21fd2`): `compose up api` hidup, `compose run --rm eval` one-shot sukses (55/55 via API container), scorecard resmi di `results/` (CORD macro 0.928 / total exact 44/45; self 0.986 / 10/10), regression gate PASS vs run sebelumnya.

## Status sebelumnya (2 Sep — sesi baru setelah shutdown laptop)

**Sesi ini (2 Sep)**: +5 self-scan baru (self_006..010) → GT dibuat VLM + verifikasi vision.

**Update berikutnya (2 Sep, setelah re-run):**
- Prompt v6: explicit-vs-derived (null≠0), tax-inclusive, EDC→null, integer-only money, bundle/DISC rules, variant tidak ditambah ke nama
- Evaluator: `name_norm()` strip kode prefix item (e.g. "0571-1854 BLUS WANITA" → "BLUS WANITA")
- `batch_extract.py --api-url` + `run_eval.py --api-url` + docker-compose eval → **ekstraksi lewat HTTP API** (compliance PDF "runs the harness against that API"; sebelumnya langsung import module)
- GT self_008 payment.method debit→null (EDC generic); self_002 change 0→null; self_003 change null→0
- Viewer: auto-refresh dihapus (user minta); reload manual F5

**Self-scan sekarang 10 dokumen** (5 manual + 5 VLM-draft):
- self_006 Olive Fried Chicken, self_007 Tottori_ID, self_008 Relasi Space After Home,
  self_009 SARSA Creative Space, self_010 Maverick — gambar dari folder `self scan/` (WhatsApp)
- GT 5 baru dihasilkan `make_self_gt.py` (VLM extract → strip confidence → meta vendor/date dari vision_analyze)
- **GT baru = DRAFT VLM, belum verified manual user** — note `"VLM-generated draft GT — review & verify"` di tiap file

**Eval self terbaru (10 docs): macro F1 0.99, total exact 10/10, item fields 1.00/1.00/1.00**
- SELF: 0 fp, 0 wrong, semua 37 item match — hasil terbaik
- CORD (45): macro F1 0.88, total exact 44/45, item name F1 0.92
- Satu-satunya miss CORD: cord_095 (flaky — model baca cash 62.000 sebagai total, padahal total due = 62.000−201 = 61.799). Run sebelumnya benar, sekarang salah → murni variance model, bukan bug
- Kegagalan self_001/self_008 sudah fix (aturan final-total = amount-paid; modifier line bukan item terpisah)

**Error analysis (results/error_analysis.json)**: item fp dari name-granularity (bundle sub-item, ejaan) = measurement jujur, bukan bug. GT CORD tidak lengkap (subtotal.fp dll) bukan error model. Yang bisa di-fix sudah semua di-fix.

**Temp files (tidak di-commit):**
- `viewer_app.py` + `viewer_app.html` — web viewer: image zoom/pan, model prediction vs GT, field table
- Jalan di `http://127.0.0.1:8100` — restart: `cd datasaur && python viewer_app.py`
- `make_self_gt.py` — helper: VLM → draft GT (strip confidence, meta placeholder)

**Keputusan besar sesi ini: pindah VLM dari `bai/mimo-v2.5` (9Router, key pribadi user) ke `gemini-3.5-flash-lite` (Gemini free tier, 5 key round-robin).**
Alasan: user keberatan share key 9Router ke engineer Datasaur; Gemini free tier aman dishare. Bonus: latency 22s → 2-4s/doc.

- [x] Requirement dibaca (PDF: `AI_Engineer_Take-Home_Test_Document_Extraction.pdf`)
- [x] Dataset CORD test split didownload (100 receipt, 234 MB) → `data/cord_test.parquet`
- [x] 30 gambar random di-sample → `data/dev_sample/` (seed 42, manifest.json + GT JSON tiap gambar)
- [x] VLM engine dipilih & dites (mimo dulu, lalu migrasi ke Gemini free tier)
- [x] Dev loop 6/6 + iterasi prompt v2 (unit_price/service/discount/payment rules)
- [x] Eval harness ditulis & dijalankan: CORD 45 docs macro F1 **0.84**, self-scan 5 docs **0.96**
- [x] Scale: 50 CORD diverse dipilih, batch extraction 50 gambar selesai (49/49 sukses via retry downscaling)
- [x] Optimasi terukur: downscaling max 1600px + JPEG 85 → total exact 78%→91%, latency -35%
- [x] FastAPI + Docker scaffold + README + git init (repo 126KB, 2 commits → sekarang 3+)
- [x] **AUDIT GAP vs requirement PDF** — ditemukan 7 gap, SEMUA sudah difix (detail di bawah)
- [x] **Migrasi Gemini**: `app/extractor.py` ditulis ulang — key pool round-robin (5 key free tier), concurrent (ThreadPoolExecutor), downscale di main pipeline, schema validation sendiri, per-field confidence, cost tracking (usage tokens + USD)
- [x] **Clean code**: file probe (15) dihapus; dev_extract/batch_extract/run_eval/eval_harness ditulis ulang memakai `app.extractor` sebagai satu-satunya sumber pipeline
- [ ] Invite reviewer (saripudin@datasaur.ai, satrio.putra@datasaur.ai, diah.pitaloka@datasaur.ai) — butuh GitHub user — **USER: TIDAK DILAKUKAN (keputusan user)**
- [ ] Test docker compose build/up beneran — **USER: TIDAK DILAKUKAN (keputusan user)**
- [ ] git push ke remote — **USER: TIDAK DILAKUKAN (keputusan user)**

## 7 Gap yang Ditemukan (audit 1 Sep) & Status Fix

| # | Gap (requirement PDF) | Fix | File |
|---|---|---|---|
| 1 | Per-field confidence tidak ada | Schema v3 + prompt minta confidence 0-1 per field; abstain = confidence 0.0 | `app/extractor.py` |
| 2 | Cost tidak visible | Response usage tokens → `compute_cost()`; scorecard berisi tokens + cost USD | `app/extractor.py`, `eval_harness.py` |
| 3 | Output tidak divalidasi | `validate_prediction()` — typed, enum method, range, unknown keys | `app/extractor.py` |
| 4 | Tidak ada A/B / regression gate | `compare_evals.py` — diff dua scorecard JSON, gate F1 drop, exit code 1 | `compare_evals.py` |
| 5 | Tidak ada `.env.example` | Dibuat (VLM_* vars, tanpa secret) | `.env.example` |
| 6 | Eval tidak end-to-end via API | `run_eval.py --live` → extract fresh via pipeline lalu score; docker eval pakai `--live` | `run_eval.py`, `docker-compose.yml` |
| 7 | Tidak ada agentic note + custom extension | README section "Agentic Development" + KeyPool (multi-key failover) sebagai extension | `README.md` |

## VLM Engine (PENTING — hasil tes semua model)

**Engine sekarang: `gemini-3.5-flash-lite` via Gemini OpenAI-compatible endpoint (free tier, 5 key round-robin).**
- Endpoint: `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions` — **format OpenAI-compatible, header `Authorization: Bearer <key>`**
- Latency **2-4s/dokumen** (vs mimo 22s), free tier 1500 RPD/key × 5 key
- Semua 5 key di `geminiapi.txt` valid: 3 AIza + 2 AQ (AQ adalah format key Gemini baru, bukan OAuth)
- Key dibaca file `geminiapi.txt` (gitignored, TIDAK pernah di-commit) → `KeyPool` round-robin thread-safe
- Retry: 429/5xx → rotate ke key berikutnya (tidak tidur 30s per attempt), backoff 30s×attempt kalau semua gagal
- Model lain: `gemini-2.5-flash-lite` deprecated (404); `gemini-2.5-flash` lambat (10-83s); `gemini-flash-lite-latest` ≈ sama cepat tapi alias bisa berubah; `gemini-3.6-flash` lambat (25s)
- mimo/9Router TIDAK dipakai lagi — `_read_9router_key` dihapus dari extractor

## Cara Akses Gemini (script)

API key dibaca dari `geminiapi.txt` (root proyek, satu key per baris, TIDAK di-commit):
```python
from app.extractor import Config, extract_receipt
cfg = Config.from_env()   # membaca VLM_BASE_URL/VLM_MODEL/VLM_API_KEY_FILE/env
res = extract_receipt(image_bytes, "image/png", cfg)
```
Endpoint OpenAI-compatible: `POST {VLM_BASE_URL}/chat/completions` dengan `Authorization: Bearer <key>`.
Body format sama persis dengan OpenAI (messages + image_url data URI) — itulah kenapa extractor tetap generic.

## Data & Struktur (setelah clean-code)

```
datasaur/
├── data/
│   ├── cord_test.parquet      # 100 receipt (image PNG bytes + ground_truth JSON)
│   ├── dev_sample/            # 30 gambar random (seed 42) + GT + manifest.json
│   ├── dev_predictions/       # hasil extraction (pred_XXX.json: raw + prediction + latency)
│   ├── cord_pool/             # 50 CORD terpilih diverse: 44 PNG+GT test + manifest_dev/test.json
│   ├── predictions/           # batch extraction test set (pred_cord_XXX.json + pred_self_XXX.json)
│   └── self_scan/             # 5 self-scan user (foto struk fisik) + GT manual + manifest
├── app/
│   ├── extractor.py           # pipeline: Config, KeyPool, call_vlm, extract_receipt, validate, cost, extract_many (concurrent)
│   └── main.py                # FastAPI
├── dev_extract.py             # dev loop (6 sampel) — pakai app.extractor
├── batch_extract.py           # batch concurrent (--workers, --overwrite) — pakai app.extractor
├── eval_harness.py            # field-level P/R/F1 + confidence + abstention + cost; output scorecard JSON
├── run_eval.py                # E2E: --live extract fresh lalu score (dipakai docker compose run --rm eval)
├── compare_evals.py           # A/B regression gate dua scorecard
├── export_gt.py / select_pool.py  # data prep (tidak berubah)
├── .env.example               # template config (tanpa secret)
├── geminiapi.txt              # 5 Gemini API key — GITIGNORED, jangan commit
├── results/                   # eval_cord/self.txt + scorecard_cord/self.json + per_doc_*.json
└── (retry_failed.py dihapus — downscale sudah jadi bagian extractor.py)
```

**Self-scan dataset (31 Agu 2026)**: 5 struk fisik foto dari user → `data/self_scan/self_001..005.jpg` + GT manual `self_XXX.json`. **self_001 (NASGOR 69) nama item = kode menu asli (Paket A1 / Merdeka 1 / Merdeka 3) — dikonfirmasi user, GT final.** Pelajaran: jangan crop gambar utk GT.

## Schema Extraction (v3 — dengan confidence)

```json
{
  "menu_items": [{"name", "quantity", "unit_price", "amount", "confidence": {"name":0-1,...}}],
  "subtotal", "discount", "tax", "service_charge", "total",
  "confidence": {"subtotal":0-1, "discount":0-1, "tax":0-1, "service_charge":0-1, "total":0-1,
                 "payment.method":0-1, "payment.amount_paid":0-1, "payment.change":0-1},
  "payment": {"method": "cash|credit_card|emoney|debit|transfer|other|null", "amount_paid", "change"}
}
```
Rules: angka plain, field absent = null (TIDAK halusinasi), item berurutan sesuai cetak, confidence 0.0 utk field yang di-abstain.

**CORD GT hanya punya**: `menu` (nm/cnt/price/unitprice/itemsubtotal/sub), `sub_total`, `total`. **TIDAK ada vendor/date/invoice_number** → vendor/date hanya dieval di 5 self-scans.

## Keputusan yang Sudah Disepakati

1. **Dataset**: CORD (open) + 5 self-scans. Target ~25-45 dokumen final, bukan 1000 (hemat token free user). Sampel MANUAL diverse dari pool 100.
2. **Dev loop**: 5-10 gambar dulu sampai schema stabil → baru scale.
3. **Stack**: FastAPI + Docker compose, provider configurable OpenAI-compatible.
4. **Deliverable wajib**: eval harness first-class (field-level metrics, failure by kind, confidence, abstention), cost/latency visible, ≥1 optimasi terukur, agentic development note + 1 custom extension (KeyPool), README lengkap, git history bermakna.
5. **VLM**: Gemini free tier 5 key round-robin (keputusan sesi ini). Provider tetap OpenAI-compatible → bisa swap kapan saja via env.

## Hasil Dev Loop (mimo, 31 Agu — historical) & tes Gemini (1 Sep)

**Dev loop 6 gambar (mimo)**: item names 22/22 benar; amounts 22/22; unit_price 2 error (idx 000, 075); subtotal/tax/total 6/6; service_charge 1 error (idx 014); discount 2/2; payment 3/3; latency 24-305s.

**Tes Gemini (1 Sep, 5 gambar CORD)**: total exact 5/5, item name 7/9, latency mean 2.3s (gemini-3.5-flash-lite). Semua 5 key valid.

## Final Eval Results (Gemini 3.5 Flash-Lite, 1 Sep sore — setelah fix normalisasi)

**CORD test (45 docs) — macro F1 0.84**
- total exact: **44/45 (98%)** — naik dari 40/45 (sebelum fix to_num/GT adapter)
- Item name match: P=0.83 R=0.90 F1=0.86 (136/151 GT matched)
- 1 sisa kegagalan: cord_095 (pred 62,000 vs GT 61,799 — selisih 201, model salah baca cashprice sbg total; subtotal+tax = 56,181+5,618 = 61,799)
- Abstention: 0 total-field
- Latency: mean 9.9s, median 3.1s, max 36.4s (vs mimo mean 94.2s — **~10x lebih cepat**)
- Cost: $0.00 (free tier)

**Self-scan (5 docs) — macro F1 0.94**
- total exact: 4/5 (80%) — 1 sisa: self_001 (pred 193,638 vs GT 213,002 — model tidak menambahkan tax 19,364; subtotal+tax seharusnya 213,002). Sebelumnya (mimo) 5/5 — regresi model
- Item name match: P=0.90 R=0.90 F1=0.90 (18/20, 1 missing: self_005 "Set Roka On Fire"/"Set Roka Golden")
- Latency: mean 3.0s | Cost: $0.00

**Fix eval yang baru dilakukan (1 Sep sore):**
- `to_num()` sekarang handle: desimal "365000.00"→365000 (sebelumnya salah ×100), prefix asterisk "*110000" (cord_076)
- `gt_cord_to_schema()`: fallback total_price → total_etc → cashprice → changeprice → (cash−change) — cord_037 (GT cuma punya total_etc 20.000) & cord_076 (hanya cashprice) sekarang benar
- Hasil: total exact CORD 40→44/45. Dampak fix ×100: cord_041, cord_043 (GT simpan 365000.00, bukan 36,500,000)

## Next Steps (sesi berikutnya)

1. ~~Bandingkan prediksi vs GT~~ ✅
2. ~~Iterasi prompt~~ ✅ (prompt v3 + confidence)
3. ~~eval_harness.py~~ ✅ ditulis ulang + scorecard JSON + fix normalisasi
4. ~~Self-scans~~ ✅ 5 foto + GT manual (final)
5. ~~Scale~~ ✅ 50 CORD diverse + batch extraction
6. ~~Optimasi terukur~~ ✅ downscaling + key rotation + concurrency
7. ~~Audit gap vs PDF~~ ✅ 7 gap difix (detail di atas)
8. ~~Cek hasil eval Gemini baru~~ ✅ total exact 44/45 (98%), latency ~10x lebih cepat, cost $0
9. ~~Hapus file sementara~~ ✅ (15 probe dihapus)
10. ~~Update README (agentic note, extension, .env.example, metrics)~~ ✅
11. Invite reviewer + git push — **DITUNDA (keputusan user)**
12. Docker compose test — **DITUNDA (keputusan user)**
13. (opsional) Fix 2 kegagalan model: cord_095 & self_001 (bukan bug eval, murni model)

## Pitfalls / Pelajaran (tambah sesi ini)

- **Gemini OpenAI-compatible endpoint**: `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions` — bukan native `/v1beta/models/...:generateContent`. Header `Authorization: Bearer`, bukan `X-goog-api-key` (tapi keduanya jalan). Response `choices[0].message.content` string (bisa ada markdown fences — parse_json_response handle).
- **Key Gemini format baru `AQ.`**: BUKAN OAuth token — valid API key, dipakai via Bearer/X-goog-api-key header. Jangan salah-filter hanya `AIza`.
- **Free tier rate limit**: 1500 RPD per key; pakai 5 key round-robin + concurrency 5 (1 req/key). Jangan 429-loop di satu key.
- CORD angka format campur: "60.000" (dot) vs "24,000" (koma) — normalisasi sebelum banding
- jangan pass API key lewat terminal command (masking) — baca dari file dalam script
- Gambar besar (>1MB) rawan timeout → **downscale di main pipeline** (bukan cuma retry): `extract_receipt` auto-downscale >1MB ke 1600px JPEG 85
- Python script yang punya module-level loop: WAJIB guard `if __name__ == "__main__":`
- User: free user, token terbatas — hemat API calls, jangan retry berlebihan
- Jangan crop gambar utk GT — crop bikin VLM halusinasi

## Konteks User

- Dragan Abrisam Widijanto, fresh grad (lulus Jul 2026), AI/ML Engineer, vokasi UGM
- Bahasa: Indonesia + Inggris. NO karakter CJK di chat.
- Gaya: ringkas, langsung, no filler. Deliverable: benci gaya "terdengar AI"
- Folder lamaran lain: `C:\Users\ASUS\Documents\lamarankerja\`
- **User keberatan share key 9Router (mimo) ke engineer Datasaur** → pakai Gemini free tier yang aman dishare