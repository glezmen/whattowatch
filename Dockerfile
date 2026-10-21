# syntax=docker/dockerfile:1

# --- Stage 1: build the Vue frontend -------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python runtime ---------------------------------------------------------
FROM python:3.13-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.13 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first (cached layer), then the project itself, installed non-editable.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-editable

COPY alembic.ini .env.example docker-entrypoint.sh ./
COPY --from=frontend /build/frontend/dist ./frontend/dist

# gosu: the entrypoint starts as root only to fix the ownership of the mounted data volume,
# then drops to the unprivileged app user (uid/gid overridable with PUID/PGID).
RUN apt-get update && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --shell /usr/sbin/nologin app \
    && chmod 755 /app/docker-entrypoint.sh \
    && mkdir -p /app/data \
    && chown app:app /app/data

# Runtime: `uv run` uses the baked venv as is (no re-sync, no network, writable cache).
ENV PATH=/app/.venv/bin:$PATH \
    UV_NO_SYNC=1 \
    UV_FROZEN=1 \
    UV_CACHE_DIR=/tmp/uv-cache \
    WTW_ENV_FILE=/app/data/.env \
    DATABASE_URL=sqlite:////app/data/whattowatch.db \
    IMPORTS_DIR=/app/data/imports \
    BACKUPS_DIR=/app/data/backups \
    FRONTEND_DIST=/app/frontend/dist \
    WTW_PORT=8000

# No USER here: docker-entrypoint.sh drops privileges with gosu after fixing the volume owner.
VOLUME ["/app/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('WTW_PORT', '8000'), timeout=4)" || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
