# =============================================================================
# Xenko WhatsApp Agent — Railway Production Dockerfile
# =============================================================================
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV HERMES_HOME=/app/.hermes
ENV HERMES_NO_COLOR=1
ENV PATH="/root/.local/bin:${PATH}"

# System dependencies (libpq for PostgreSQL / GBrain)
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
    && rm -rf /var/lib/apt/lists/*

# Install Hermes Agent
RUN curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash

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

# WhatsApp session dir (mount Railway volume here)
RUN mkdir -p /app/whatsapp-sessions /app/.hermes

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:${PORT:-3000}/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
