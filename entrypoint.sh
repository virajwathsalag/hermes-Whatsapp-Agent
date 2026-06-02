#!/bin/bash
set -euo pipefail

HERMES_DIR="${HERMES_HOME:-/app/.hermes}"
SESSION_DIR="${HERMES_DIR}/platforms/whatsapp/session"
CREDS_FILE="${SESSION_DIR}/creds.json"
PORT="${PORT:-3000}"

mkdir -p "${SESSION_DIR}" /app/whatsapp-sessions

# Patch gateway port for Railway
if [ -f "${HERMES_DIR}/config.yaml" ]; then
  sed -i "s/port: 3000/port: ${PORT}/" "${HERMES_DIR}/config.yaml" 2>/dev/null || true
fi

# Hermes reads WhatsApp + LLM keys from ~/.hermes/.env (not Railway env alone)
ENV_FILE="${HERMES_DIR}/.env"
if [ ! -f "${ENV_FILE}" ]; then
  touch "${ENV_FILE}"
fi

_ensure_env() {
  local key="$1"
  local val="$2"
  if [ -n "${val}" ] && ! grep -q "^${key}=" "${ENV_FILE}" 2>/dev/null; then
    echo "${key}=${val}" >> "${ENV_FILE}"
  fi
}

_ensure_env "WHATSAPP_ENABLED" "${WHATSAPP_ENABLED:-true}"
_ensure_env "WHATSAPP_MODE" "${WHATSAPP_MODE:-bot}"
_ensure_env "WHATSAPP_ALLOW_ALL_USERS" "${WHATSAPP_ALLOW_ALL_USERS:-true}"
_ensure_env "GATEWAY_ALLOW_ALL_USERS" "${GATEWAY_ALLOW_ALL_USERS:-true}"
_ensure_env "MINIMAX_API_KEY" "${MINIMAX_API_KEY:-}"
_ensure_env "OPENAI_API_KEY" "${OPENAI_API_KEY:-}"
_ensure_env "ANTHROPIC_API_KEY" "${ANTHROPIC_API_KEY:-}"
_ensure_env "OPENROUTER_API_KEY" "${OPENROUTER_API_KEY:-}"
_ensure_env "MODEL_PROVIDER" "${MODEL_PROVIDER:-minimax}"
_ensure_env "MODEL_NAME" "${MODEL_NAME:-MiniMax-M2.1}"
_ensure_env "AIRTABLE_PAT" "${AIRTABLE_PAT:-}"

# Keep /app/whatsapp-sessions in sync if an old volume was mounted there
if [ -d /app/whatsapp-sessions ] && [ "$(ls -A /app/whatsapp-sessions 2>/dev/null)" ] && [ ! -f "${CREDS_FILE}" ]; then
  echo "==> Migrating session files from /app/whatsapp-sessions"
  cp -a /app/whatsapp-sessions/. "${SESSION_DIR}/" 2>/dev/null || true
fi

_start_health_stub() {
  python3 - <<'PY' &
import http.server
import os
import socketserver

port = int(os.environ.get("PORT", "8080"))

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/health"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args):
        pass

with socketserver.TCPServer(("0.0.0.0", port), Handler) as httpd:
    httpd.serve_forever()
PY
  HEALTH_STUB_PID=$!
}

_stop_health_stub() {
  if [ -n "${HEALTH_STUB_PID:-}" ]; then
    kill "${HEALTH_STUB_PID}" 2>/dev/null || true
  fi
}

echo "==> Xenko WhatsApp Agent (HERMES_HOME=${HERMES_DIR}, PORT=${PORT})"

if [ ! -f "${CREDS_FILE}" ]; then
  echo "==> WhatsApp not paired yet — starting QR pairing wizard"
  echo "==> Scan the QR code below: WhatsApp -> Settings -> Linked Devices -> Link a Device"
  _start_health_stub
  trap _stop_health_stub EXIT
  hermes whatsapp || {
    echo "==> Pairing wizard exited (code $?). Check logs for QR or errors."
    echo "==> Tip: Railway Shell -> run: hermes whatsapp"
  }
  _stop_health_stub
  trap - EXIT
fi

if [ ! -f "${CREDS_FILE}" ]; then
  echo "ERROR: Still no creds.json at ${CREDS_FILE}"
  echo "Mount a Railway volume at: ${SESSION_DIR}"
  echo "Or pair locally and upload session files to that path."
  exit 1
fi

echo "==> WhatsApp session found — starting gateway"
exec hermes gateway run
