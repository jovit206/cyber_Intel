FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY lab-16.1 ./lab-16.1
COPY lab-16.2 ./lab-16.2
WORKDIR /app/lab-16.2

CMD ["python", "-m", "app.entrypoint"]
