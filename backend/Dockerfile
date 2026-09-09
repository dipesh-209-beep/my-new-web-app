FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Stable server by default (no --reload). Local dev hot-reloads via the
# --reload command added by docker-compose.override.yml (auto-loaded by
# `docker compose` in dev); a Deployment that runs this image directly or
# with the override excluded gets a normal, non-restarting uvicorn.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
