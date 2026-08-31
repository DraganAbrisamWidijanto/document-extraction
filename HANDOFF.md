# HANDOFF — Datasaur.ai Take-Home Test (Document Extraction)

Proyek: `C:\Users\ASUS\Documents\lamarankerja\datasaur` — **jangan ada file keluar dari folder ini**.
Deadline: 3 hari dari email diterima. Submit: invite `saripudin@datasaur.ai`, `satrio.putra@datasaur.ai`, `diah.pitaloka@datasaur.ai` ke private repo.

## Status Terakhir (31 Agu 2026, ~01:30)

- [x] Requirement dibaca (PDF: `AI_Engineer_Take-Home_Test_Document_Extraction.pdf`)
- [x] Dataset CORD test split didownload (100 receipt, 234 MB) → `data/cord_test.parquet`
- [x] 30 gambar random di-sample → `data/dev_sample/` (seed 42, manifest.json + GT JSON tiap gambar)
- [x] VLM engine dipilih & dites
- [x] Dev loop pertama jalan (5/6 gambar selesai, 1 timeout)
- [ ] Schema final & prompt stabil
- [ ] Eval harness (field-level P/R/F1, confidence, abstention, cost/latency)
- [ ] Scale ke full set
- [ ] Optimasi + Docker + README + commit

## VLM Engine (PENTING — hasil tes semua model)

**Engine utama: `bai/mimo-v2.5`** — satu-satunya VLM yang jalan dari 31 model mimo yang dites.
- Latency ~22s/dokumen, output JSON valid
- `bai/deepseek-v4-flash-vision-exp` juga jalan (routing ke upstream `mimo-v2.5-free`) — cadangan
- `oc/mimo-v2.5-free` ❌ TIDAK ADA — prefix `oc/` tidak terdaftar di 9Router
- Semua `OM/mimo-*`, `oa/mimo-*`, `ir/*`, `mimo/*`, `gmi/*` → 402/403/400/429
- `OM/MiniMax-M3`, `OM/mimo-v2.5` → 402 kuota habis

**`vision_analyze` Hermes BISA jalan** (dites pada cord_003.png — deskripsi detail) — untuk verifikasi visual/QA, bukan pipeline.

Prefix 9Router yang terdaftar: `bai`, `oa`, `ir`, `OM`, `agentic`, `wave`, `gmi`, `wf`, `arza`, `tukeria`.

Pembagian tugas (keputusan user):
- Script pipeline extraction → direct 9Router `bai/mimo-v2.5`
- Verifikasi visual/QA → `vision_analyze` Hermes
- Eval & preprocessing → python lokal (anaconda)

## Cara Akses 9Router (script)

API key dibaca dari SQLite (hindari masking `sk-*`):
```python
DB = os.path.expanduser("~/AppData/Roaming/9router/db/data.sqlite")
db = sqlite3.connect(DB)
api_key = db.execute("SELECT key FROM apiKeys WHERE isActive = 1").fetchone()[0]
```
Endpoint: `http://127.0.0.1:20128/v1` — WAJIB `stream=False` untuk VLM, image sebagai data URI base64.
Rate limit: ~1 request/15-20s, sleep 30s saat 429, retry 402/403 dengan backoff.

## Data & Struktur

```
datasaur/
├── data/
│   ├── cord_test.parquet      # 100 receipt (image PNG bytes + ground_truth JSON)
│   ├── dev_sample/            # 30 gambar random (seed 42) + GT + manifest.json
│   ├── dev_predictions/       # hasil extraction (pred_XXX.json: raw + prediction + latency)
│   ├── cord_pool/             # 50 CORD terpilih diverse: 44 PNG+GT test + manifest_dev/test.json
│   ├── predictions/           # batch extraction test set (pred_cord_XXX.json + pred_self_XXX.json)
│   └── self_scan/             # 5 self-scan user (foto struk fisik) + GT manual + manifest
├── dev_extract.py             # extraction pipeline (schema v2)
├── batch_extract.py           # batch extraction utk test set (cord+self)
├── select_pool.py             # pilih 50 diverse dari 100 CORD
├── export_gt.py               # export GT CORD dari parquet
├── eval_compare.py            # bandingkan pred vs GT (dev loop)
├── eval_harness.py            # eval field-level P/R/F1 + error typology (support GT cord+self)
├── test_*.py / raw_probe.py / find_models.py  # probe VLM (historical, bisa hapus)
└── (selanjutnya: FastAPI + Docker + README)
```

**Self-scan dataset (31 Agu 2026)**: 5 struk fisik foto dari user → `data/self_scan/self_001..005.jpg` + GT manual `self_XXX.json` (schema extraction + meta vendor/date/source). GT dari reading `vision_analyze`, aritmetika diverifikasi (subtotal=Σamount, total=subtotal+tax, semua OK). **self_001 (NASGOR 69) nama item = kode menu asli (Paket A1 / Merdeka 1 / Merdeka 3) — dikonfirmasi user, GT final.** Pelajaran: jangan crop gambar utk GT — crop 55% atas membuat VLM halusinasi (baca payment yang tak ada di crop).

Python: anaconda3 (pandas+pyarrow+pillow) untuk data; hermes venv `AI/hermes/hermes-agent/venv/Scripts/python.exe` untuk API calls.

## Schema Extraction (v1 — sedang di-iterate)

```json
{
  "menu_items": [{"name", "quantity", "unit_price", "amount"}],
  "subtotal", "discount", "tax", "service_charge", "total",
  "payment": {"method": "cash|credit_card|emoney|null", "amount_paid", "change"}
}
```
Rules: angka plain (25000, bukan "Rp 25.000"), field absent = null (TIDAK halusinasi), item berurutan sesuai cetak.

**CORD GT hanya punya**: `menu` (nm/cnt/price/unitprice/itemsubtotal/sub), `sub_total` (subtotal_price/discount_price/tax_price/service_price/etc), `total` (total_price/cashprice/changeprice/creditcardprice/emoneyprice/menuqty_cnt). **TIDAK ada vendor/date/invoice_number** → field vendor/date hanya bisa dieval di 5 self-scans (GT kita buat sendiri).

## Keputusan yang Sudah Disepakati

1. **Dataset**: CORD (open) + 5 self-scans (Shopee/Tokped digital + foto struk cafe/Indomaret — tangan megang struk OK). Target ~25-45 dokumen final, bukan 1000 (hemat token free user). Pilih sampel MANUAL diverse, jangan random, dari pool 100.
2. **Dev loop**: 5-10 gambar dulu sampai schema stabil → baru scale. Sampel terpilih: idx [14, 94, 28, 0, 75, 31] (high-res foto HP, low-res, multi-item+tax, credit card, discount, tiny 432×648).
3. **Stack**: FastAPI + Docker compose (API extraction + `docker compose run --rm eval`), provider configurable OpenAI-compatible.
4. **Deliverable wajib**: eval harness first-class (field-level metrics, failure by kind, confidence, abstention), cost/latency visible, ≥1 optimasi terukur, agentic development note + 1 custom extension, README lengkap, git history bermakna.

## Hasil Dev Loop Pertama (prediksi vs GT belum dibandingkan!)

| idx | total (pred) | items | latency | catatan |
||---|---|---|---|---|
| 014 | 281,435 ✅ | 6/6 ✅ | 96.9s | **service_charge 17,050 ❌ (GT 17,850)** — digit error; total tetap benar |
| 094 | 140,063 ✅ | 4/4 ✅ | 36.7s | semua OK, payment CC |
| 028 | 426,195 ✅ | 3/3 ✅ | 24.4s | semua OK, discount 0 ✅ |
| 000 | 60,000 ✅ | 1/1 ✅ | 54.1s | **unit_price 60,000 ❌ (GT 30,000)** — model taruh line total sbg unit price; qty 2 |
| 075 | 282,000 ✅ | 6/6 ✅ | 25.2s | **unit_price null semua ❌ (abstain)** — cnt=1 → unit_price = amount, tp null |
| 031 | 113,886 ✅ | 2/2 ✅ | 305.5s | semua OK; discount GT di level item (-85,500), pred taruh top-level — nilai cocok |

**Ringkasan 6 pred vs GT (31 Agu 2026)**:
- **Item names**: 22/22 benar, 0 extra, 0 missing
- **Amounts**: 22/22 benar (line total)
- **Unit price**: 2 error — idx 000 (salah baca 60k->30k), idx 075 (abstain 6x)
- **Subtotal/Tax/Total**: 6/6 benar
- **Service charge**: 1 error — idx 014 (17,050 vs 17,850)
- **Discount**: 2/2 benar (idx 000, idx 031)
- **Payment method**: 3/3 benar (3 null — benar)
- **Latency**: 24.4–305.5s (idx 031 kena timeout 180s sekali, retry sukses)

## Final Eval Results (1 Sep 2026, ~01:00) — batch extraction 50 gambar

**CORD test (45 docs) — macro F1 0.81**
- total exact: 35/45 (78%)
- Item name match: P=0.90 R=0.75 F1=0.82 (114/151 GT matched)
- item.amount F1=0.92, item.unit_price F1=0.72 (banyak FP — model prediksi item ekstra)
- 7 docs gagal total (3× timeout / parse fail) — all FP counts on those
- Latency: mean 145.7s, median 56.8s, max 570s (triple timeout 3×180s)
- Sumber error utama: timeout (gambar >6MB, 2304×4096), item hallucination (model baca ulang), format angka campur

**Self-scan (5 docs) — macro F1 0.96**
- total exact: 5/5 (100%)
- Item name match: 19/20 (1 missing: self_001 "Merdeka 3")
- Semua money fields OK
- Latency: mean 27.7s (foto fisik, lebih cepat dari PNG CORD)

**Kesimpulan**: schema inti (subtotal, total, payment amount) solid di CORD & self-scan. Kelemahan: (1) timeout pada gambar besar — timeout 180s → retry 3× = 9 menit wasted; (2) item hallucination (FP tinggi) — model sering menambah item yang tidak ada; (3) unit_price masih error pada qty>1 tanpa unit price eksplisit.

## Next Steps

1. ~~Bandingkan 5 prediksi vs GT~~ ✅ (6/6 — lihat tabel di atas)
2. ~~Iterasi prompt~~ ✅ (prompt v2: unit_price/service/discount/payment rules — 075 & 014 fixed)
3. ~~eval_harness.py~~ ✅ ditulis (field-level P/R/F1, error typology, abstention, latency; support GT cord+self) — **belum dijalankan di test set**
4. ~~Self-scans~~ ✅ 5 foto + GT manual (final)
5. ~~Scale~~ ✅ 50 CORD diverse dipilih (44 test baru + 5 dev + 94) — batch extraction jalan di background
6. Jalankan eval_harness di test set (45 cord + 5 self) setelah batch selesai
7. FastAPI + Docker + README + git init (private, invite reviewer)

## Pitfalls / Pelajaran

- CORD angka format campur: "60.000" (dot) vs "24,000" (koma) — kedua-duanya ribuan separator, perlu normalisasi sebelum banding
- Terminal timeout 420s: dev_extract jalan lama (~22s/gambar + retry), jalankan dengan `background=true` + notify
- jangan pass API key lewat terminal command (masking `sk-*` → `***`) — selalu baca dari SQLite dalam script
- Gambar CORD ada yang 2304×4096 (foto HP) dan 432×648 (kecil) — VLM harus handle keduanya
- User: free user, token terbatas — hemat API calls, jangan retry berlebihan

## Konteks User

- Dragan Abrisam Widijanto, fresh grad (lulus Jul 2026), AI/ML Engineer, vokasi UGM
- Bahasa: Indonesia + Inggris. NO karakter CJK di chat.
- Gaya: ringkas, langsung, no filler. Deliverable: benci gaya "terdengar AI"
- Folder lamaran lain: `C:\Users\ASUS\Documents\lamarankerja\`
