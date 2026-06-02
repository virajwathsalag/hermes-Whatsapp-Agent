#!/bin/bash
# Xenko Railway entrypoint v2.2 — no hermes whatsapp auto-run
set -uo pipefail

HERMES_DIR="${HERMES_HOME:-/app/.hermes}"
SESSION_DIR="${HERMES_DIR}/platforms/whatsapp/session"
ALT_SESSION_DIR="${HERMES_DIR}/whatsapp/session"
CREDS_FILE="${SESSION_DIR}/creds.json"
PORT="${PORT:-3000}"

echo "==> entrypoint v2.3 (session restore + long WhatsApp timeouts)"

mkdir -p "${SESSION_DIR}" "${ALT_SESSION_DIR}" /app/whatsapp-sessions

if [ -f "${HERMES_DIR}/config.yaml" ]; then
  sed -i "s/port: 3000/port: ${PORT}/" "${HERMES_DIR}/config.yaml" 2>/dev/null || true
fi

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
  local b64
  b64="$(_collect_b64)"
  local len=${#b64}
  if [ "${len}" -lt 50 ]; then
    echo "==> WHATSAPP_SESSION_B64 not set or too short (len=${len})"
    return 1
  fi
  echo "==> Restoring session from base64 (len=${len})"
  if ! printf '%s' "${b64}" | base64 -d > /tmp/wa-session.tgz 2>/dev/null; then
    echo "WARN: base64 decode failed"
    return 1
  fi
  if tar -xzf /tmp/wa-session.tgz -C "${SESSION_DIR}" 2>/dev/null; then
    rm -f /tmp/wa-session.tgz
    echo "==> Extracted session to ${SESSION_DIR}"
    ls -la "${SESSION_DIR}" 2>/dev/null || true
    _mirror_session_dirs
    return 0
  fi
  if printf '%s' "${b64}" | base64 -d > "${CREDS_FILE}" 2>/dev/null; then
    echo "==> Wrote creds.json only"
    _mirror_session_dirs
    return 0
  fi
  rm -f /tmp/wa-session.tgz
  echo "WARN: could not extract session archive"
  return 1
}

_restore_session_from_url() {
  [ -n "${WHATSAPP_SESSION_URL:-}" ] || return 1
  echo "==> Downloading session from WHATSAPP_SESSION_URL"
  if curl -fsSL "${WHATSAPP_SESSION_URL}" -o /tmp/wa-session.tgz && \
     tar -xzf /tmp/wa-session.tgz -C "${SESSION_DIR}" 2>/dev/null; then
    rm -f /tmp/wa-session.tgz
    _mirror_session_dirs
    return 0
  fi
  return 1
}

_mirror_session_dirs() {
  mkdir -p "${HERMES_DIR}/whatsapp" "${HERMES_DIR}/platforms/whatsapp"
  rm -rf "${ALT_SESSION_DIR}" 2>/dev/null || true
  cp -a "${SESSION_DIR}" "${ALT_SESSION_DIR}" 2>/dev/null || true
}

# Legacy volume at wrong path
if [ -d /app/whatsapp-sessions ] && [ "$(ls -A /app/whatsapp-sessions 2>/dev/null)" ] && [ ! -f "${CREDS_FILE}" ]; then
  echo "==> Migrating /app/whatsapp-sessions -> ${SESSION_DIR}"
  cp -a /app/whatsapp-sessions/. "${SESSION_DIR}/" 2>/dev/null || true
  _mirror_session_dirs
fi

_restore_session_from_b64 || true
_restore_session_from_url || true

_start_health_stub() {
  python3 - <<'PY' &
import http.server, os, socketserver
port = int(os.environ.get("PORT", "8080"))
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/health"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok\n")
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *a): pass
socketserver.TCPServer(("0.0.0.0", port), H).serve_forever()
PY
  echo $! > /tmp/health_stub.pid
}

_stop_health_stub() {
  [ -f /tmp/health_stub.pid ] && kill "$(cat /tmp/health_stub.pid)" 2>/dev/null || true
  rm -f /tmp/health_stub.pid
}

echo "==> Xenko WhatsApp Agent (HERMES_HOME=${HERMES_DIR}, PORT=${PORT})"

if [ ! -f "${CREDS_FILE}" ]; then
  echo ""
  echo "============================================================"
  echo "  WhatsApp session missing at:"
  echo "  ${CREDS_FILE}"
  echo ""
  echo "  1) Run: scripts/encode-local-session.ps1  (~3KB file)"
  echo "  2) Railway Variables -> WHATSAPP_SESSION_B64"
  echo "  3) Volume mount: ${SESSION_DIR}"
  echo "  4) Redeploy (must see 'entrypoint v2.2' in logs)"
  echo ""
  echo "  Container waiting (no crash loop)..."
  echo "============================================================"
  _start_health_stub
  while [ ! -f "${CREDS_FILE}" ]; do
    sleep 30
    _restore_session_from_b64 || true
    _restore_session_from_url || true
  done
  _stop_health_stub
fi

echo "==> creds.json OK — starting gateway"
exec hermes gateway run
