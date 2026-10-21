#!/usr/bin/env bash
# Container entrypoint: prepare the data volume, migrate, then serve (or run a given command,
# e.g. `docker compose run --rm whattowatch uv run wtw refresh run`).
set -euo pipefail

DATA_DIR=/app/data
ENV_FILE="${WTW_ENV_FILE:-$DATA_DIR/.env}"
PORT="${WTW_PORT:-8000}"

if ! mkdir -p "$DATA_DIR" "${IMPORTS_DIR:-$DATA_DIR/imports}" "${BACKUPS_DIR:-$DATA_DIR/backups}"; then
  echo "ERROR: $DATA_DIR is not writable by uid $(id -u); fix the owner of the mounted ./data directory" >&2
  exit 1
fi

# Secrets written from the Settings page live here, on the volume (never in the image).
if [[ ! -f "$ENV_FILE" ]]; then
  mkdir -p "$(dirname "$ENV_FILE")"
  (umask 077 && cp /app/.env.example "$ENV_FILE")
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE from .env.example - add API keys in Settings in the web UI"
fi

# Creates the database on first start, applies migrations, seeds the providers.
uv run wtw db upgrade

if [[ $# -gt 0 ]]; then
  exec "$@"
fi

# `wtw serve` binds 127.0.0.1; inside the container all interfaces must be bound.
exec uv run uvicorn whattowatch.api.app:create_app --factory --host 0.0.0.0 --port "$PORT"
