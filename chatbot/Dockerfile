# AI Threat Assistant - standalone image (jovit206/chatbot)
# Contains only the assistant: FastAPI backend + static frontend. Ollama and MongoDB run outside the container.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Application code and assets only (see .dockerignore). No .env or credentials are copied.
COPY app.py README.md ARCHITECTURE.md Dockerfile docker-compose.yml ./
COPY backend ./backend
COPY public ./public

RUN useradd --create-home --uid 10001 chatbot && chown -R chatbot:chatbot /app
USER chatbot

# Defaults suitable for Docker Desktop; override at run time (-e / --env-file / compose).
ENV LLM_PROVIDER=ollama \
    OLLAMA_BASE_URL=http://host.docker.internal:11434 \
    OLLAMA_CHAT_MODEL=llama3.2:3b \
    OLLAMA_KEEP_ALIVE=30m \
    LLM_TIMEOUT_SECONDS=180 \
    MONGO_DATABASE=chapter07 \
    FRONTEND_ORIGIN=http://localhost:8000 \
    SESSION_COOKIE_SECURE=false

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
