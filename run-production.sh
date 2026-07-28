#!/usr/bin/env bash
# Start (or restart) the MCP Claude gateway in production via PM2.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

DOMAIN="${MCP_DOMAIN:-mcp.claude.lee-associates-southflorida.com}"
VENV_PYTHON="$ROOT/venv/bin/python"
PM2_APP="mcp-claude"

if [[ ! -f "$ROOT/.env" ]]; then
  echo "error: .env not found. Copy .env.example and fill in credentials." >&2
  exit 1
fi

if [[ ! -x "$VENV_PYTHON" ]] || ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
  echo "Creating virtualenv..."
  rm -rf venv
  python3 -m venv venv
fi

echo "Installing dependencies..."
"$VENV_PYTHON" -m pip install -q -r requirements.txt

if ! command -v pm2 >/dev/null 2>&1; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "error: npm not found. Install with: sudo apt-get install -y nodejs npm" >&2
    exit 1
  fi
  echo "Installing pm2..."
  sudo npm install -g pm2
fi

if pm2 describe "$PM2_APP" >/dev/null 2>&1; then
  echo "Restarting $PM2_APP..."
  pm2 restart ecosystem.config.cjs --update-env
else
  echo "Starting $PM2_APP..."
  pm2 start ecosystem.config.cjs
fi

pm2 save

echo
echo "Production MCP server is running."
echo "  Local health:  http://127.0.0.1:8000/health"
echo "  Public URL:    https://$DOMAIN"
echo "  Endpoints:     /enformion /zoominfo /parcelscraper /adminsite"
echo
pm2 status "$PM2_APP"
