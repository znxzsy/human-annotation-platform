FROM python:3.11-slim

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir .

ENV ANNOTATION_DB=/data/review.sqlite3 \
    ANNOTATION_AUDIT=/data/audit.jsonl \
    ANNOTATION_EXPORTS=/data/exports

VOLUME ["/data"]
EXPOSE 18068
CMD ["sh", "-c", "python scripts/seed_demo.py --db /data/review.sqlite3 --audit /data/audit.jsonl && exec python -m annotation_platform.server --host 0.0.0.0 --port 18068 --db /data/review.sqlite3 --audit /data/audit.jsonl --exports /data/exports"]
