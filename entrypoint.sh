#!/bin/bash
# Xenko Railway entrypoint v2.4 — always-on /health for Railway
set -uo pipefail

HERMES_DIR="${HERMES_HOME:-/app/.hermes}"
SESSION_DIR="${HERMES_DIR}/platforms/whatsapp/session"
ALT_SESSION_DIR="${HERMES_DIR}/whatsapp/session"
CREDS_FILE="${SESSION_DIR}/creds.json"
RAILWAY_PORT="${PORT:-8080}"

echo "==> entrypoint v2.6 (health=8080, bridge=3000, gateway=8642)"

mkdir -p "${SESSION_DIR}" "${ALT_SESSION_DIR}" /app/whatsapp-sessions

# Gateway stays on 3000 internally — Railway health uses RAILWAY_PORT separately
# Do NOT rewrite gateway port to PORT (that breaks WhatsApp bridge on 3000)

ENV_FILE="${HERMES_DIR}/.env"
touch "${ENV_FILE}"

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
_ensure_env "HERMES_GATEWAY_PLATFORM_CONNECT_TIMEOUT" "${HERMES_GATEWAY_PLATFORM_CONNECT_TIMEOUT:-180}"
_ensure_env "WHATSAPP_NPM_INSTALL_TIMEOUT" "${WHATSAPP_NPM_INSTALL_TIMEOUT:-600}"
_ensure_env "HERMES_WHATSAPP_HTTP_TIMEOUT" "${HERMES_WHATSAPP_HTTP_TIMEOUT:-120}"
_ensure_env "WHATSAPP_DEBUG" "${WHATSAPP_DEBUG:-true}"
_ensure_env "WHATSAPP_BRIDGE_PORT" "${WHATSAPP_BRIDGE_PORT:-3000}"
_ensure_env "API_SERVER_ENABLED" "${API_SERVER_ENABLED:-false}"

_collect_b64() {
  local b64=""
  if [ -n "${WHATSAPP_SESSION_B64:-}" ]; then
    b64="${WHATSAPP_SESSION_B64}"
  elif [ -n "${WHATSAPP_SESSION_B64_PARTS:-}" ]; then
    local i=1
    local parts="${WHATSAPP_SESSION_B64_PARTS}"
    while [ "${i}" -le "${parts}" ]; do
      local var="WHATSAPP_SESSION_B64_${i}"
      local chunk
      chunk="$(eval "printf '%s' \"\${${var}:-}\"")"
      b64="${b64}${chunk}"
      i=$((i + 1))
    done
  fi
  printf '%s' "${b64}"
}

_restore_session_from_b64() {
  local b64 len
  b64="$(_collect_b64)"
  len=${#b64}
  if [ "${len}" -lt 50 ]; then
    return 1
  fi
  echo "==> Restoring session from base64 (len=${len})"
  if ! printf '%s' "${b64}" | base64 -d > /tmp/wa-session.tgz 2>/dev/null; then
    return 1
  fi
  if tar -xzf /tmp/wa-session.tgz -C "${SESSION_DIR}" 2>/dev/null; then
    rm -f /tmp/wa-session.tgz
    _mirror_session_dirs
    return 0
  fi
  rm -f /tmp/wa-session.tgz
  return 1
}

_restore_session_from_url() {
  [ -n "${WHATSAPP_SESSION_URL:-}" ] || return 1
  curl -fsSL "${WHATSAPP_SESSION_URL}" -o /tmp/wa-session.tgz && \
    tar -xzf /tmp/wa-session.tgz -C "${SESSION_DIR}" 2>/dev/null && \
    rm -f /tmp/wa-session.tgz && _mirror_session_dirs
}

_mirror_session_dirs() {
  mkdir -p "${HERMES_DIR}/whatsapp" "${HERMES_DIR}/platforms/whatsapp"
  rm -rf "${ALT_SESSION_DIR}" 2>/dev/null || true
  cp -a "${SESSION_DIR}" "${ALT_SESSION_DIR}" 2>/dev/null || true
}

if [ -d /app/whatsapp-sessions ] && [ "$(ls -A /app/whatsapp-sessions 2>/dev/null)" ] && [ ! -f "${CREDS_FILE}" ]; then
  cp -a /app/whatsapp-sessions/. "${SESSION_DIR}/" 2>/dev/null || true
  _mirror_session_dirs
fi

_restore_session_from_b64 || true
_restore_session_from_url || true

# Railway healthcheck requires /health on $PORT — Hermes gateway does NOT provide this
_start_health_server() {
  export RAILWAY_PORT
  python3 - <<'PY' &
import http.server, os, socketserver
port = int(os.environ.get("RAILWAY_PORT", "8080"))
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/health"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n")
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *a): pass
socketserver.TCPServer(("0.0.0.0", port), H).serve_forever()
PY
  echo $! > /tmp/health_server.pid
  echo "==> Health server listening on 0.0.0.0:${RAILWAY_PORT}/health"
}

_stop_health_server() {
  [ -f /tmp/health_server.pid ] && kill "$(cat /tmp/health_server.pid)" 2>/dev/null || true
  rm -f /tmp/health_server.pid
}

_start_health_server

echo "==> Xenko WhatsApp Agent (HERMES_HOME=${HERMES_DIR})"

if [ ! -f "${CREDS_FILE}" ]; then
  echo "==> Waiting for WhatsApp session at ${CREDS_FILE}"
  while [ ! -f "${CREDS_FILE}" ]; do
    sleep 30
    _restore_session_from_b64 || true
    _restore_session_from_url || true
  done
fi

echo "==> creds.json OK — starting Hermes gateway"
echo "==> First boot may take 1-3 min (npm + WhatsApp bridge). Send a WhatsApp test after you see 'Bridge ready'."
echo "==> Required vars: MINIMAX_API_KEY, AIRTABLE_PAT"
if [ -z "${MINIMAX_API_KEY:-}" ] && [ -z "${OPENROUTER_API_KEY:-}" ] && [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "WARN: No LLM API key in Railway Variables — bot cannot reply to messages"
fi

export PYTHONUNBUFFERED=1
exec hermes gateway run
