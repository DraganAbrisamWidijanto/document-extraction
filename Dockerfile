# Receipt extraction API + eval harness
FROM python:3.11-slim

WORKDIR /app

# system deps (for Pillow in eval; API itself only needs stdlib)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo libwebp7 zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY eval_harness.py batch_extract.py dev_extract.py export_gt.py select_pool.py run_eval.py ./
COPY data/ ./data/

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
