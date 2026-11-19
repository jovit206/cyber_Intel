
FROM python:3.12-slim

ARG APP_VERSION=1.0.0
ARG INSTALL_VECTOR_LAB=false
ARG INSTALL_CHAPTER18_POLYGLOT=false

LABEL org.opencontainers.image.title="Threat Intelligence Platform (Labs 7-18)" \
      org.opencontainers.image.description="FastAPI threat-intelligence dashboard with MongoDB operations, vector projects, and optional comparative multi-database threat analysis" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    APP_VERSION=${APP_VERSION}

WORKDIR /app

# Install main application dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install optional Lab 17 dependencies
COPY lab-17/requirements-vector-lab.txt ./lab-17/requirements-vector-lab.txt
COPY lab-18/requirements-chapter18.txt ./lab-18/requirements-chapter18.txt

# Install CPU-only PyTorch to avoid unnecessary CUDA dependencies
RUN if [ "$INSTALL_VECTOR_LAB" = "true" ]; then \
    pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch && \
    pip install --no-cache-dir -r lab-17/requirements-vector-lab.txt; \
    fi

RUN if [ "$INSTALL_CHAPTER18_POLYGLOT" = "true" ]; then \
    pip install --no-cache-dir -r lab-18/requirements-chapter18.txt; \
    fi

# Application, frontend and project documentation
# No .env, secrets or database files are copied
COPY backend ./backend
COPY public ./public
COPY lab8 ./lab8
COPY lab9 ./lab9
COPY lab10 ./lab10
COPY lab-11 ./lab-11
COPY lab-13 ./lab-13
COPY lab-14 ./lab-14
COPY lab-15 ./lab-15
COPY lab-16 ./lab-16
COPY lab-17 ./lab-17
COPY lab-18 ./lab-18
# Project documentation and deployment files: searched by the AI Threat Assistant's platform knowledge base.
COPY README.md ADMIN_GUIDE.md BILLING_AND_ENTITLEMENTS.md SECURITY_CENTER.md docker-compose.yml Dockerfile ./

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
