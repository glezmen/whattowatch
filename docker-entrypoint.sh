#!/usr/bin/env bash
# Container entrypoint: prepare the data volume, migrate, then serve (or run a given command,
# e.g. `docker compose run --rm whattowatch uv run wtw refresh run`).
set -euo pipefail

DATA_DIR=/app/data
ENV_FILE="${WTW_ENV_FILE:-$DATA_DIR/.env}"
PORT="${WTW_PORT:-8000}"
APP_USER=app

# Running as root (the image sets no USER): align the app user with PUID/PGID if given so files
# on the bind-mounted volume belong to the host user, make the volume writable, then re-exec
# this script as the app user via gosu. If the container was started with `--user`, we are not
# root and simply continue as that user.
if [[ "$(id -u)" == "0" ]]; then
  PUID="${PUID:-1000}"
  PGID="${PGID:-1000}"
  if [[ "$(id -g "$APP_USER")" != "$PGID" ]]; then
    groupmod -o -g "$PGID" "$APP_USER"
  fi
  if [[ "$(id -u "$APP_USER")" != "$PUID" ]]; then
    usermod -o -u "$PUID" "$APP_USER"
  fi
  mkdir -p "$DATA_DIR" "${IMPORTS_DIR:-$DATA_DIR/imports}" "${BACKUPS_DIR:-$DATA_DIR/backups}"
  # Only touch what is not already owned by the app user (cheap on big volumes).
  find "$DATA_DIR" \( ! -user "$PUID" -o ! -group "$PGID" \) -exec chown "$PUID:$PGID" {} + 2>/dev/null || true
  chown "$PUID:$PGID" /tmp 2>/dev/null || true
  exec gosu "$APP_USER" "$0" "$@"
fi

if ! mkdir -p "$DATA_DIR" "${IMPORTS_DIR:-$DATA_DIR/imports}" "${BACKUPS_DIR:-$DATA_DIR/backups}"; then
  echo "ERROR: $DATA_DIR is not writable by uid $(id -u); start the container as root (default) or fix the owner of ./data" >&2
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
