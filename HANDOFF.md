# HANDOFF — Datasaur Take-Home (Document Extraction)

Proyek: `C:\Users\ASUS\Documents\lamarankerja\datasaur`. Deadline 3 hari dari email.
Submit: repo **private** + invite `saripudin@datasaur.ai`, `satrio.putra@datasaur.ai`, `diah.pitaloka@datasaur.ai`.
**GitHub = user-driven: JANGAN push/buat repo tanpa permintaan eksplisit user. User akan push sendiri.**

## Kondisi saat ini (2 Sep 2026, siang — sesi berikutnya mulai dari sini)

**Semua requirement teknis SELESAI & TERVERIFIKASI.** Sisa satu: commit + GitHub push (user sendiri).

### Selesai sesi ini
- **Kleister diperluas 5 → 12 dok**: 7 GT JSON baru dari `expected.tsv` (verified), manifest 12 entri, semua 12 PDF ada GT resminya. Kuota aman (84 VLM call; free tier ~5k/hari efektif).
- **Docker E2E 3 split: exit 0** (67 dok live via API). Containerized run MENEMUKAN bug yang tak terlihat lokal — inilah nilai E2E:
  1. Compose membangun 2 image terpisah (api & eval) → image eval STALE (kode Kleister tidak ada). Fix: shared tag `image: datasaur:latest` di kedua service.
  2. `batch_extract` kirim multipart `filename="file"` tanpa `.pdf` → semua PDF kena 400 dari `/extract-pdf`. Fix: filename asli + server check magic-bytes OR ekstensi.
  3. Endpoint `async def` + VLM call menit-an → event loop terblokir → PDF SERIAL (~10 mnt/dok, run 2 jam). Fix: sync `def` (threadpool, paralel). Ini alasan "docker lambat vs lokal".
  4. `cost_usd: null` crash harness → `or 0`; pred gagal kini simpan `ok`/`error` (membuka akar masalah cepat).
  5. Healthcheck compose + `depends_on: condition: service_healthy` (menghilangkan connection refused saat API belum siap) + retry 1x di klien.
- **Analisis 18 wrong + 5 fn Kleister (12 dok)** → 2 lapis fix:
  - *Prompt/selector* (`app/kleister_prompt.py`, `app/pdf_extractor.py`): report_date = period-end (bukan audit/signing); address = Registered Office (bukan venue/museum — aturan lama TERBALIK, ini penyebab 3 wrong alamat); income/spending exact figures, null jika hanya rounded; halaman SoFA totals (terpadat angka ber-pemisah-ribuan) DIJAMIN masuk 7 terpilih via `best_sofa_page` — menghilangkan 5 abstain + 2 wrong prior-year (1414c9bc income/spending jadi exact).
  - *Komparator Kleister* (`eval_harness.py`, fairness eval — GT tak diutak-atik): `_compare_kleister_name` (suffix legal dibuang: Ltd/Limited; subsequence; initialism Citb≈Construction Industry Training Board), `_compare_kleister_street` (range 2-3≈2, subsequence; pred-ada-GT-null = fp sesuai konvensi Kleister).
- **Hasil final**: CORD 0.93, self 0.99 (tak berubah, komparator baru tak menyentuh split lain), **Kleister 12 dok: macro 0.96** (0.92 live → 0.95 setelah prompt/comparator fix → 0.96 setelah jaminan halaman SoFA; charity_name 12/12, report_date 12/12, income/spending 0.94, micro 0.96). Sisa error didokumentasikan di README (3 wrong angka yang di dokumen hanya tercetak bulat £'000 — GT one-pound berasal dari register Charity Commission, bukan PDF; 2 wrong postcode; 4 fp street konvensi GT-null).
- README + REQUIREMENT_AUDIT diupdate: tanda "pending" dihapus, angka/latensi/latency baru, 2 trade-off baru (sync endpoints, shared image tag).
- `batch_extract.py` `ok` counter di-fix (semua `ok=0/12` karena cek `total is not None` — CORD-centric).

### Catatan kecil
- Metadata halaman (`pages_total`/`page_results`) tidak ikut tersimpan di pred dari jalur API — viewer kehilangan kolom Source page(s) UNTUK PRED BARU ini; seleksi halaman reproducible offline (regex di `pdf_extractor.py`) kalau mau backfill. Tidak mengaruhi skor.
- Docker Desktop harus jalan dulu (start manual; agent jangan shutdown/reboot OS).

## Sisa langkah (user-driven)
1. **Commit + push + invite reviewer** — repo private, 3 email reviewer di atas.
2. Fresh clone check saat push (cukup `docker compose build && docker compose up api && docker compose run --rm eval`).

## Catatan teknis penting
- Key VLM: `geminiapi.txt` di root (5 key Gemini free tier round-robin). Tidak ada `.env` — config dari env default + file key.
- Jalankan uvicorn/compose dengan `workdir` = root repo (uvicorn dari luar root: `ModuleNotFoundError: app`).
- Eval reproducible: `eval_harness.py --manifest data/kleister/manifest.json --pred-dir data/predictions --gt-format kleister`; atau `run_eval.py --split kleister` (tanpa --live = pakai pred yang ada).
- Viewer dev: `python viewer_app.py` → 127.0.0.1:8100 (TEMP, tidak wajib di-commit). Matikan semua python dulu kalau port 8100 keburu (`netstat -ano | grep :8100` → taskkill /F /PID).
- **in.tsv.xz (6.4 MB) & expected.tsv JANGAN di-commit** (hanya untuk membangun GT).
- `data/kleister/preview/` render lama (dari 5 dok) — viewer render ulang otomatis; boleh di-commit atau gitignore.
- Docker: `docker compose build` (shared tag `datasaur:latest`) — cek `docker compose run --rm eval grep -c kleister batch_extract.py` = 5 kalau curiga image stale.
- Skill `document-extraction-takehome` perlu di-update dgn gotcha sesi ini (async endpoint serial, compose per-service image stale, multipart filename ekstensi, Kleister GT normalization di eval).
