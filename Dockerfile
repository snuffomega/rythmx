# syntax=docker/dockerfile:1
# =============================================================================
# Rythmx — Production Multi-Stage Dockerfile
# =============================================================================
# Build:  docker build -t rythmx .
# Run:    docker compose up -d
#
# PUID / PGID (LinuxServer.io convention):
#   Unraid:   PUID=99  PGID=100  (default in docker-compose.yml)
#   TrueNAS:  PUID=568 PGID=568
#   Linux:    PUID=1000 PGID=1000 (default if unset)
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Build the React frontend
# ---------------------------------------------------------------------------
FROM node:20.20.0-alpine AS builder
WORKDIR /build
COPY frontend/package*.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: Python runtime — uvicorn serves the built React app
# ---------------------------------------------------------------------------
FROM python:3.12.13-slim

LABEL org.opencontainers.image.title="Rythmx"
LABEL org.opencontainers.image.description="Music enrichment and discovery platform"

WORKDIR /rythmx

# Install gosu for privilege de-escalation in entrypoint
RUN apt-get update && \
    apt-get install -y --no-install-recommends gosu && \
    rm -rf /var/lib/apt/lists/* && \
    gosu nobody true

# Install Python dependencies (prod only — no pytest, no httpx)
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# Copy application code (tests/ and scripts/ excluded via .dockerignore)
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY plugins/ ./plugins/

# Copy the compiled React app into the webui/ folder FastAPI serves
COPY --from=builder /build/dist ./webui/

# Copy and prepare entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# PUID/PGID defaults — override via environment in docker-compose.yml
ENV PUID=1000
ENV PGID=1000

# Secrets and data are provided at runtime via env vars and volume mounts.
# Never bake .env or db files into the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8009

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8009/health')"]

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8009"]
