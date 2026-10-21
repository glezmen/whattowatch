#!/usr/bin/env bash
# One-shot launcher: sets up everything from scratch (if needed) and starts the app.
# Usage: ./start.sh [--no-open] [--no-build] [--port 8000]
set -euo pipefail

cd "$(dirname "$0")"

PORT=8000
OPEN=1
BUILD=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-open) OPEN=0 ;;
    --no-build) BUILD=0 ;;
    --port) PORT="$2"; shift ;;
    -h|--help) sed -n '2,3p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# 1. Tooling
command -v uv >/dev/null 2>&1 || die "uv is required (https://docs.astral.sh/uv/). On macOS: brew install uv"
command -v npm >/dev/null 2>&1 || die "Node.js/npm is required for the web UI. On macOS: brew install node"

# 2. Python dependencies (uv downloads the right Python if missing)
say "Python dependencies (uv sync)"
uv sync --quiet

# 3. .env
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  say "Created .env from .env.example - add your API keys there or in Settings in the UI"
fi

# 4. Frontend
if [[ $BUILD -eq 1 ]]; then
  if [[ ! -d frontend/node_modules ]]; then
    say "Frontend dependencies (npm install)"
    (cd frontend && npm install --silent)
  fi
  say "Building the web UI"
  (cd frontend && npm run build --silent >/dev/null)
elif [[ ! -f frontend/dist/index.html ]]; then
  die "frontend/dist is missing; run without --no-build once"
fi

# 5. Database (creates data/whattowatch.db, applies migrations, seeds providers)
say "Database migrations"
uv run wtw db upgrade

# 6. Port check
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  die "Port $PORT is already in use. Stop the other server or use --port <n>."
fi

# 7. Start
URL="http://127.0.0.1:$PORT"
say "Starting whattowatch at $URL (Ctrl+C to stop)"
if [[ $OPEN -eq 1 ]]; then
  ( sleep 2; if command -v open >/dev/null 2>&1; then open "$URL"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"; fi ) &
fi
exec uv run uvicorn whattowatch.api.app:create_app --factory --host 127.0.0.1 --port "$PORT"
