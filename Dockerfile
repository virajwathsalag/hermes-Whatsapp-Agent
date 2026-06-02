# =============================================================================
# XENKO WhatsApp Agent - FULL RAILWAY DEPLOYMENT
# =============================================================================
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV HERMES_HOME=/app/.hermes

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    bash \
    python3 \
    python3-pip \
    python3-venv \
    git \
    wget \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Hermes Agent
RUN curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash

# Working directory
WORKDIR /app

# Copy everything from repo
COPY .hermes/ /app/.hermes/
COPY soul.md /app/soul.md
COPY airtable_crm.py /app/airtable_crm.py
COPY gbrain_history.py /app/gbrain_history.py
COPY plugins/ /app/plugins/

# Create WhatsApp session directory (persisted via Railway volume)
RUN mkdir -p /app/whatsapp-sessions

# Set PATH
ENV PATH="/root/.local/bin:${PATH}"

# Expose port
EXPOSE 3000

# Run command
CMD ["hermes", "gateway", "run"]