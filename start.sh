#!/usr/bin/env bash
# One-shot launcher: sets up everything from scratch (if needed) and starts the app.
# Usage: ./start.sh [--no-open] [--no-build] [--port 8000] [--host 127.0.0.1|0.0.0.0] [--install-node]
# Installs uv automatically if missing; --install-node installs Node.js 22 via NodeSource (Ubuntu/Debian, sudo).
set -euo pipefail

cd "$(dirname "$0")"

PORT=8000
HOST=127.0.0.1
OPEN=1
BUILD=1
INSTALL_NODE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-open) OPEN=0 ;;
    --no-build) BUILD=0 ;;
    --port) PORT="$2"; shift ;;
    --host) HOST="$2"; shift ;;
    --lan) HOST=0.0.0.0 ;;
    --install-node) INSTALL_NODE=1 ;;
    -h|--help) sed -n '2,3p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# 1. Tooling
# uv: not in apt; the official installer puts it in ~/.local/bin without sudo.
if ! command -v uv >/dev/null 2>&1; then
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
if ! command -v uv >/dev/null 2>&1; then
  say "uv not found - installing it with the official installer (no sudo, into ~/.local/bin)"
  command -v curl >/dev/null 2>&1 || die "curl is required to install uv (Ubuntu: sudo apt-get install -y curl)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || die "uv installation failed; see https://docs.astral.sh/uv/getting-started/installation/"
fi

# Node.js >= 20.19 is required by the Vite build. Ubuntu's apt "nodejs" is usually too old.
node_ok() {
  command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 || return 1
  local v; v=$(node -p 'process.versions.node' 2>/dev/null) || return 1
  [[ "${v%%.*}" -ge 22 ]] || { [[ "${v%%.*}" -eq 20 ]] && [[ "$(echo "$v" | cut -d. -f2)" -ge 19 ]]; }
}
if ! node_ok; then
  if [[ $INSTALL_NODE -eq 1 ]] && command -v apt-get >/dev/null 2>&1; then
    say "Installing Node.js 22 from NodeSource (needs sudo)"
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
    sudo apt-get install -y nodejs
  else
    cat >&2 <<'MSG'
ERROR: Node.js >= 20.19 (with npm) is required to build the web UI.
  Ubuntu/Debian:  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs
                  (or re-run this script with --install-node to do that for you)
  macOS:          brew install node
MSG
    exit 1
  fi
  node_ok || die "Node.js installation failed"
fi

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
port_busy() {
  if command -v lsof >/dev/null 2>&1; then lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1
  elif command -v ss >/dev/null 2>&1; then ss -ltn 2>/dev/null | grep -q ":$PORT "
  else return 1; fi
}
if port_busy; then
  die "Port $PORT is already in use. Stop the other server or use --port <n>."
fi

# 7. Start
URL="http://127.0.0.1:$PORT"
if [[ "$HOST" != "127.0.0.1" && "$HOST" != "localhost" ]]; then
  say "Binding to $HOST: the app is reachable from your whole local network - set profile passwords in Settings -> Profile"
fi
say "Starting whattowatch at $URL (Ctrl+C to stop)"
if [[ $OPEN -eq 1 ]]; then
  ( sleep 2; if command -v open >/dev/null 2>&1; then open "$URL"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"; fi ) &
fi
exec uv run uvicorn whattowatch.api.app:create_app --factory --host "$HOST" --port "$PORT"
