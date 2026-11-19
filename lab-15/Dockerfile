FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TASKTRACKER_DB_NAME=tasktracker

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

RUN mkdir -p /app/logs /app/backups /app/reports \
    && groupadd --system --gid 10001 tasktracker \
    && useradd --system --uid 10001 --gid tasktracker --home-dir /nonexistent tasktracker \
    && chown -R tasktracker:tasktracker /app

USER tasktracker:tasktracker

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
