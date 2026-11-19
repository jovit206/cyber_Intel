FROM python:3.12-slim

ARG APP_VERSION=1.0.0
LABEL org.opencontainers.image.title="Threat Intelligence Platform (Labs 7, 8, 9)" \
      org.opencontainers.image.description="FastAPI threat-intelligence dashboard with MongoDB replica-set monitoring (Lab 8) and write-concern / network-partition experiments (Lab 9)" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    APP_VERSION=${APP_VERSION}

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application, frontend and the Lab 7/8/9 scripts. No .env, secrets or database files are copied
# (see .dockerignore); configuration is supplied at run time through environment variables.
COPY backend ./backend
COPY public ./public
COPY labs ./labs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
