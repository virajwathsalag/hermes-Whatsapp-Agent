#!/bin/bash
# Xenko Railway entrypoint v2.8 — clean env, session preserve, bridge diagnostics
set -uo pipefail

HERMES_DIR="${HERMES_HOME:-/app/.hermes}"
SESSION_DIR="${HERMES_DIR}/platforms/whatsapp/session"
ALT_SESSION_DIR="${HERMES_DIR}/whatsapp/session"
CREDS_FILE="${SESSION_DIR}/creds.json"
RAILWAY_PORT="${PORT:-8080}"
BRIDGE_LOG_PATHS=(
  "${HERMES_DIR}/whatsapp/bridge.log"
  "${HERMES_DIR}/platforms/whatsapp/bridge.log"
)

echo "==> entrypoint v2.8 (clean env + session preserve + bridge diagnostics)"

mkdir -p "${SESSION_DIR}" "${ALT_SESSION_DIR}" /app/whatsapp-sessions

ENV_FILE="${HERMES_DIR}/.env"

_write_clean_env() {
  # Replace bloated .env (Railway can leave 200+ stale keys that break allowlist)
  local tmp
  tmp="$(mktemp)"
  {
    echo "# Xenko Railway — written fresh each boot"
    echo "WHATSAPP_ENABLED=${WHATSAPP_ENABLED:-true}"
    echo "WHATSAPP_MODE=${WHATSAPP_MODE:-bot}"
    echo "WHATSAPP_ALLOW_ALL_USERS=${WHATSAPP_ALLOW_ALL_USERS:-true}"
    echo "WHATSAPP_ALLOWED_USERS=${WHATSAPP_ALLOWED_USERS:-*}"
    echo "GATEWAY_ALLOW_ALL_USERS=${GATEWAY_ALLOW_ALL_USERS:-true}"
    echo "MODEL_PROVIDER=${MODEL_PROVIDER:-minimax}"
    echo "MODEL_NAME=${MODEL_NAME:-MiniMax-M2.1}"
    echo "HERMES_GATEWAY_PLATFORM_CONNECT_TIMEOUT=${HERMES_GATEWAY_PLATFORM_CONNECT_TIMEOUT:-180}"
    echo "WHATSAPP_NPM_INSTALL_TIMEOUT=${WHATSAPP_NPM_INSTALL_TIMEOUT:-600}"
    echo "HERMES_WHATSAPP_HTTP_TIMEOUT=${HERMES_WHATSAPP_HTTP_TIMEOUT:-120}"
    echo "WHATSAPP_DEBUG=${WHATSAPP_DEBUG:-true}"
    echo "WHATSAPP_BRIDGE_PORT=${WHATSAPP_BRIDGE_PORT:-3000}"
    echo "FOUNDER_WHATSAPP_PHONE=${FOUNDER_WHATSAPP_PHONE:-94760193094}"
    echo "HOME_WHATSAPP_PHONE=${HOME_WHATSAPP_PHONE:-94741597999}"
    echo "HERMES_PRIVILEGED_PHONE=${HERMES_PRIVILEGED_PHONE:-+947****3094}"
    echo "API_SERVER_ENABLED=${API_SERVER_ENABLED:-false}"
    [ -n "${MINIMAX_API_KEY:-}" ] && echo "MINIMAX_API_KEY=${MINIMAX_API_KEY}"
    [ -n "${OPENAI_API_KEY:-}" ] && echo "OPENAI_API_KEY=${OPENAI_API_KEY}"
    [ -n "${ANTHROPIC_API_KEY:-}" ] && echo "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}"
    [ -n "${OPENROUTER_API_KEY:-}" ] && echo "OPENROUTER_API_KEY=${OPENROUTER_API_KEY}"
    [ -n "${AIRTABLE_PAT:-}" ] && echo "AIRTABLE_PAT=${AIRTABLE_PAT}"
    [ -n "${POSTGRES_PASSWORD:-}" ] && echo "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}"
    [ -n "${GBRAIN_PG_HOST:-}" ] && echo "GBRAIN_PG_HOST=${GBRAIN_PG_HOST}"
    [ -n "${GBRAIN_PG_PORT:-}" ] && echo "GBRAIN_PG_PORT=${GBRAIN_PG_PORT}"
    [ -n "${GBRAIN_PG_DB:-}" ] && echo "GBRAIN_PG_DB=${GBRAIN_PG_DB}"
    [ -n "${GBRAIN_PG_USER:-}" ] && echo "GBRAIN_PG_USER=${GBRAIN_PG_USER}"
  } > "${tmp}"
  mv "${tmp}" "${ENV_FILE}"
}

_write_clean_env

_export_runtime_env() {
  export HERMES_HOME="${HERMES_DIR}"
  export WHATSAPP_ENABLED="${WHATSAPP_ENABLED:-true}"
  export WHATSAPP_MODE="${WHATSAPP_MODE:-bot}"
  export WHATSAPP_ALLOW_ALL_USERS="${WHATSAPP_ALLOW_ALL_USERS:-true}"
  export WHATSAPP_ALLOWED_USERS="${WHATSAPP_ALLOWED_USERS:-*}"
  export GATEWAY_ALLOW_ALL_USERS="${GATEWAY_ALLOW_ALL_USERS:-true}"
  export WHATSAPP_DEBUG="${WHATSAPP_DEBUG:-true}"
  export WHATSAPP_BRIDGE_PORT="${WHATSAPP_BRIDGE_PORT:-3000}"
  export PYTHONUNBUFFERED=1
  [ -n "${MINIMAX_API_KEY:-}" ] && export MINIMAX_API_KEY
  [ -n "${AIRTABLE_PAT:-}" ] && export AIRTABLE_PAT
}

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

# Volume mount: copy persisted session in if creds not in primary path yet
if [ -d /app/whatsapp-sessions ] && [ "$(ls -A /app/whatsapp-sessions 2>/dev/null)" ] && [ ! -f "${CREDS_FILE}" ]; then
  cp -a /app/whatsapp-sessions/. "${SESSION_DIR}/" 2>/dev/null || true
  _mirror_session_dirs
fi

# Only restore from B64 when no session on disk — do NOT wipe volume each boot
if [ ! -f "${CREDS_FILE}" ]; then
  _restore_session_from_b64 || true
  _restore_session_from_url || true
else
  echo "==> Using existing session on disk (skip B64 restore to preserve lid-mapping files)"
  _mirror_session_dirs
fi

_log_paired_number() {
  local f
  f="$(find "${SESSION_DIR}" "${ALT_SESSION_DIR}" -maxdepth 1 -name 'device-list-*.json' 2>/dev/null | head -1)"
  if [ -n "${f}" ]; then
    local num
    num="$(basename "${f}" | sed 's/device-list-//;s/.json//')"
    echo "==> Paired WhatsApp number: +${num}  (message THIS number from a different phone)"
  fi
  local lid_count
  lid_count="$(find "${SESSION_DIR}" "${ALT_SESSION_DIR}" -maxdepth 1 -name 'lid-mapping-*' 2>/dev/null | wc -l | tr -d ' ')"
  echo "==> Session files: creds=$([ -f "${CREDS_FILE}" ] && echo yes || echo no), lid-mappings=${lid_count}"
  if [ "${lid_count}" = "0" ]; then
    echo "WARN: No lid-mapping files — re-export session with scripts/encode-local-session.ps1 and update WHATSAPP_SESSION_B64"
  fi
}

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

_start_bridge_diagnostics() {
  (
    sleep 95
    echo "==> Post-bridge diagnostics:"
    if curl -sf --max-time 5 "http://127.0.0.1:3000/health" >/dev/null 2>&1; then
      echo "==> Bridge HTTP /health: OK"
    elif curl -sf --max-time 5 "http://127.0.0.1:3000/" >/dev/null 2>&1; then
      echo "==> Bridge HTTP root: OK"
    else
      echo "WARN: Bridge HTTP not responding on 127.0.0.1:3000 (gateway may still be polling)"
    fi
    local log=""
    for p in "${BRIDGE_LOG_PATHS[@]}"; do
      if [ -f "${p}" ]; then
        log="${p}"
        break
      fi
    done
    if [ -n "${log}" ]; then
      echo "==> bridge.log tail (${log}):"
      tail -30 "${log}" 2>/dev/null | sed 's/^/    /'
    else
      echo "==> bridge.log not found yet (check after first message with WHATSAPP_DEBUG=true)"
    fi
    echo "==> Gateway idle — no logs until someone messages the bot number. Send a test now."
  ) &
  (
    local log=""
    for p in "${BRIDGE_LOG_PATHS[@]}"; do
      [ -f "${p}" ] && log="${p}" && break
    done
    [ -z "${log}" ] && exit 0
    for _ in $(seq 1 90); do
      [ -f "${log}" ] && break
      sleep 2
    done
    [ -f "${log}" ] || exit 0
    tail -n 0 -F "${log}" 2>/dev/null | while IFS= read -r line; do
      case "${line}" in
        *message*|*Message*|*incoming*|*Incoming*|*denied*|*Denied*|*allowed*|*Allowed*)
          echo "[bridge] ${line}"
          ;;
      esac
    done
  ) &
}

_start_health_server
_start_bridge_diagnostics

echo "==> Xenko WhatsApp Agent (HERMES_HOME=${HERMES_DIR})"

if [ ! -f "${CREDS_FILE}" ]; then
  echo "==> Waiting for WhatsApp session at ${CREDS_FILE}"
  while [ ! -f "${CREDS_FILE}" ]; do
    sleep 30
    _restore_session_from_b64 || true
    _restore_session_from_url || true
  done
fi

_log_paired_number

echo "==> creds.json OK — starting Hermes gateway"
echo "==> Plugins: $(ls -1 ${HERMES_DIR}/plugins 2>/dev/null | tr '\n' ' ')"
echo "==> .env keys: $(grep -c '^[A-Z]' "${ENV_FILE}" 2>/dev/null || echo 0) (clean file, was bloated before v2.8)"
if grep -q '^MINIMAX_API_KEY=.\+' "${ENV_FILE}" 2>/dev/null; then
  echo "==> MINIMAX_API_KEY: present"
else
  echo "ERROR: MINIMAX_API_KEY missing — add it in Railway Variables and redeploy"
fi
grep -q '^WHATSAPP_ALLOW_ALL_USERS=true' "${ENV_FILE}" && echo "==> WHATSAPP_ALLOW_ALL_USERS=true (all senders allowed)"
echo "==> After 'Bridge ready': gateway waits silently. Test from another phone, not the paired device."
echo "==> Send to the bot number above: I need marketing help"

_export_runtime_env
exec hermes gateway run
