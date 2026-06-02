# =============================================================================
# Xenko WhatsApp Agent — Railway Production Dockerfile
# =============================================================================
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV HERMES_HOME=/app/.hermes
ENV HERMES_NO_COLOR=1
ENV PATH="/root/.local/bin:${PATH}"

# System dependencies + Node.js (required for WhatsApp Baileys bridge)
RUN apt-get update && apt-get install -y \
    curl \
    bash \
    python3 \
    python3-pip \
    python3-venv \
    git \
    wget \
    libpq-dev \
    libcrypt1 \
    libssl3 \
    ca-certificates \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && node --version && npm --version

# Install Hermes Agent
RUN curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash

# Pre-install WhatsApp bridge npm deps (avoids 30s connect timeout on Railway cold start)
RUN BRIDGE="$(find /root /opt -path '*/scripts/whatsapp-bridge/package.json' 2>/dev/null | head -1)" && \
    if [ -n "${BRIDGE}" ]; then \
      cd "$(dirname "${BRIDGE}")" && npm install --silent && \
      echo "Pre-installed WhatsApp bridge at $(pwd)"; \
    else \
      echo "WARN: whatsapp-bridge not found during build"; \
    fi

# Longer timeouts for slow cloud startup (also set in Railway Variables)
ENV HERMES_GATEWAY_PLATFORM_CONNECT_TIMEOUT=180
ENV WHATSAPP_NPM_INSTALL_TIMEOUT=600
ENV HERMES_WHATSAPP_HTTP_TIMEOUT=120

WORKDIR /app

# Hermes config, skills, and security policy
COPY .hermes/ /app/.hermes/
COPY soul.md /app/.hermes/SOUL.md

# Custom plugins (must live under HERMES_HOME/plugins)
COPY plugins/ /app/.hermes/plugins/

# Helper scripts
COPY airtable_crm.py /app/airtable_crm.py
COPY gbrain_history.py /app/gbrain_history.py
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# WhatsApp session path used by Hermes (volume mount target)
RUN mkdir -p /app/.hermes/platforms/whatsapp/session /app/whatsapp-sessions

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=5 \
    CMD curl -f "http://localhost:${PORT:-8080}/health" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
