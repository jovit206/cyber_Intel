FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MONGO_URI="" \
    MONGODB_ALERT_WEBHOOK_URL="" \
    MONGODB_ALERT_WEBHOOK_PLATFORM=slack

WORKDIR /app/chapter14

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && curl -fsSL https://pgp.mongodb.com/server-8.0.asc \
        | gpg --dearmor -o /usr/share/keyrings/mongodb-server-8.0.gpg \
    && echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] https://repo.mongodb.org/apt/debian bookworm/mongodb-org/8.0 main" \
        > /etc/apt/sources.list.d/mongodb-org-8.0.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends mongodb-database-tools \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt monitoring_dashboard.py backup_verify.py backup_worker.py ./

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt \
    && groupadd --system --gid 10001 chapter14 \
    && useradd --system --uid 10001 --gid chapter14 --home-dir /nonexistent chapter14 \
    && mkdir -p logs backups reports \
    && chown -R chapter14:chapter14 /app/chapter14 \
    && mongodump --version \
    && mongorestore --version

USER chapter14:chapter14

CMD ["python", "monitoring_dashboard.py"]
