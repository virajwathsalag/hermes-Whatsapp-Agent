#!/bin/bash
set -euo pipefail

mkdir -p /app/whatsapp-sessions /app/.hermes

# Railway injects $PORT; Hermes reads port from config.yaml
PORT="${PORT:-3000}"
if [ -f /app/.hermes/config.yaml ]; then
  sed -i "s/port: 3000/port: ${PORT}/" /app/.hermes/config.yaml 2>/dev/null || true
fi

echo "==> Xenko WhatsApp Agent starting (HERMES_HOME=${HERMES_HOME}, PORT=${PORT})"

exec hermes gateway run
