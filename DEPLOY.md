# Xenko WhatsApp Agent — Railway Deployment Guide

Complete step-by-step guide to deploy the Xenko WhatsApp sales agent on Railway.

**Repo:** https://github.com/virajwathsalag/hermes-Whatsapp-Agent  
**Stack:** Hermes Agent + Baileys WhatsApp + Airtable CRM + PostgreSQL (GBrain)

---

## Prerequisites

Before you start, gather these:

| Item | Where to get it |
|------|-----------------|
| Railway account | https://railway.app |
| GitHub account | To connect the repo |
| MiniMax API key | https://platform.minimax.io (or OpenRouter/OpenAI) |
| Airtable PAT | Airtable → Account → Developer hub → Personal access tokens |
| Airtable base ID | Already configured: `appPcG5glvZZ8dxVQ` |
| WhatsApp phone | Business phone to scan QR code |
| Railway Starter plan ($5/mo) | Required for persistent volumes |

---

## Step 1 — Push the repo to GitHub

This folder (`hermes/docker/railway/`) is a standalone deploy repo.

```bash
cd hermes/docker/railway

# If not already connected to GitHub:
git remote -v
# Should show: https://github.com/virajwathsalag/hermes-Whatsapp-Agent

git add .
git commit -m "Railway-ready deployment package"
git push origin main
```

---

## Step 2 — Create Railway project

1. Go to https://railway.app/dashboard
2. Click **New Project**
3. Choose **Deploy from GitHub repo**
4. Select `virajwathsalag/hermes-Whatsapp-Agent`
5. Railway detects `Dockerfile` and `railway.toml` automatically

---

## Step 3 — Add PostgreSQL database

1. In your Railway project, click **+ New**
2. Select **Database → PostgreSQL**
3. Wait for it to provision (~30 seconds)
4. Click the Postgres service → **Variables** tab
5. Note these values (you'll reference them next):
   - `PGHOST`
   - `PGPORT`
   - `PGUSER`
   - `PGPASSWORD`
   - `PGDATABASE`

---

## Step 4 — Set environment variables on Hermes service

Click your **Hermes app service** (not Postgres) → **Variables** tab.

Paste these variables. Use **Reference Variables** for Postgres where shown:

```env
# Runtime
HERMES_HOME=/app/.hermes
HERMES_NO_COLOR=1
LOG_LEVEL=info

# LLM (MiniMax)
MODEL_PROVIDER=minimax
MODEL_NAME=MiniMax-M2.1
MINIMAX_API_KEY=<your-minimax-key>

# Airtable CRM
AIRTABLE_PAT=<your-airtable-pat>
AIRTABLE_BASE_ID=appPcG5glvZZ8dxVQ
AIRTABLE_LEADS_TABLE=tbltPK2QoQJ6LCptt
AIRTABLE_PEOPLE_TABLE=tblfogtDSlN3dMBBI
AIRTABLE_COMPANIES_TABLE=tblCTSMzQMrXFRK6Y
AIRTABLE_OPPORTUNITIES_TABLE=tblJBjdAbOSQj2NPc

# PostgreSQL — use Railway reference syntax:
POSTGRES_HOST=${{Postgres.PGHOST}}
POSTGRES_PORT=${{Postgres.PGPORT}}
POSTGRES_DB=${{Postgres.PGDATABASE}}
POSTGRES_USER=${{Postgres.PGUSER}}
POSTGRES_PASSWORD=${{Postgres.PGPASSWORD}}
GBRAIN_PG_HOST=${{Postgres.PGHOST}}
GBRAIN_PG_PORT=${{Postgres.PGPORT}}
GBRAIN_PG_DB=${{Postgres.PGDATABASE}}
GBRAIN_PG_USER=${{Postgres.PGUSER}}

# WhatsApp
WHATSAPP_SESSION_DIR=/app/whatsapp-sessions
HERMES_PRIVILEGED_PHONE=+94760193094
```

> **Tip:** Replace `Postgres` with your actual Postgres service name if different.

See `.env.example` for the full list including optional Twenty CRM variables.

---

## Step 5 — Add persistent volumes (critical)

WhatsApp sessions are lost on every restart without volumes.

1. Click your **Hermes app service**
2. Go to **Settings → Volumes**
3. Click **+ Add Volume**

**Volume 1 — WhatsApp sessions:**
| Setting | Value |
|---------|-------|
| Mount path | `/app/whatsapp-sessions` |
| Size | 1 GB |

**Volume 2 — Hermes state (recommended):**
| Setting | Value |
|---------|-------|
| Mount path | `/app/.hermes` |
| Size | 1 GB |

> Volumes require Railway **Starter plan** ($5/month) or higher.

---

## Step 6 — Configure networking

1. Hermes service → **Settings → Networking**
2. Click **Generate Domain** (optional, for health checks)
3. Confirm internal port is **3000** (set automatically via `railway.toml`)

---

## Step 7 — Deploy

Railway auto-deploys on every push to `main`. For manual deploy:

1. Hermes service → **Deployments**
2. Click **Deploy** (or push to GitHub)

Watch the build logs. A successful deploy shows:
```
==> Xenko WhatsApp Agent starting (HERMES_HOME=/app/.hermes, PORT=3000)
```

---

## Step 8 — Connect WhatsApp (first time only)

1. Open **Deploy Logs** for the running deployment
2. Look for a QR code in the logs (appears within ~60 seconds)
3. On your business phone: WhatsApp → Settings → Linked Devices → Link a Device
4. Scan the QR code
5. Session saves to `/app/whatsapp-sessions` volume — survives restarts

If QR expires, restart the service and scan again.

---

## Step 9 — Verify everything works

### Health check
```bash
curl https://YOUR-APP.up.railway.app/health
```
Expected: `200 OK`

### WhatsApp intake test
Send a message to the connected WhatsApp number:
```
I need marketing help
```

Expected agent reply:
```
hey what's your company called and what do you sell
```

### Airtable CRM test
Complete the 5-step intake. Check your Airtable **Leads** table for a new record.

### Logs to look for
```
xenko-crm: registered crm_add_lead
xenko-whatsapp-guard: intake guard + output sanitizer enabled
```

---

## Architecture

```
WhatsApp Client
      │
      ▼
┌─────────────────┐     ┌──────────────┐
│  Hermes Gateway │────▶│  Airtable    │
│  (this service) │     │  CRM         │
└────────┬────────┘     └──────────────┘
         │
         ▼
┌─────────────────┐
│  PostgreSQL     │
│  (GBrain memory)│
└─────────────────┘
```

---

## Repo structure

```
.
├── Dockerfile              # Production build
├── entrypoint.sh           # Startup script (PORT + dirs)
├── railway.toml            # Railway config (healthcheck, builder)
├── .env.example            # All env vars (copy to Railway dashboard)
├── soul.md                 # Agent identity → copied to .hermes/SOUL.md
├── airtable_crm.py         # CRM helper script
├── gbrain_history.py       # Conversation history sync
├── .hermes/
│   ├── config.yaml         # Gateway, WhatsApp, model config
│   ├── config_security.yaml# Privileged phone numbers
│   └── skills/             # xenko-sales, client-qualifier
└── plugins/
    ├── xenko-crm/          # Airtable write-only CRM tool
    └── xenko-whatsapp-guard/ # 5-step intake enforcement
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| QR code not appearing | Check logs for errors; ensure volume mounted at `/app/whatsapp-sessions` |
| Agent uses generic persona | Verify `SOUL.md` exists at `/app/.hermes/SOUL.md` (fixed in Dockerfile) |
| CRM not saving leads | Check `AIRTABLE_PAT` is set; verify in Railway Variables |
| Postgres connection errors | Confirm `${{Postgres.PGHOST}}` references match your Postgres service name |
| WhatsApp disconnects on restart | Add volume at `/app/whatsapp-sessions`; upgrade to Starter plan |
| `crm_add_lead` tool missing | Redeploy — plugins must be at `/app/.hermes/plugins/` |
| Health check failing | Wait 90s (start period); check port 3000 is exposed |

---

## Optional: Twenty CRM (separate Railway project)

Twenty runs as its own Railway service. After deploying Twenty:

1. Log into Twenty → Settings → API → Create API key
2. Add to Hermes service variables:
   ```env
   TWENTY_API_KEY=<jwt-from-twenty>
   TWENTY_API_URL=https://your-twenty.up.railway.app
   ```

Store the API key only in Railway Variables — not in code (see `RAILWAY_FIX.md` in parent hermes folder).

---

## Local testing before deploy

```bash
cd hermes/docker/railway

docker build -t xenko-whatsapp .

docker run -p 3000:3000 \
  -v ./whatsapp-sessions:/app/whatsapp-sessions \
  -v ./.hermes-local:/app/.hermes \
  -e AIRTABLE_PAT=your_pat \
  -e MINIMAX_API_KEY=your_key \
  -e POSTGRES_HOST=your_host \
  -e POSTGRES_PORT=your_port \
  -e POSTGRES_PASSWORD=your_password \
  xenko-whatsapp
```

Scan QR in terminal output, then test via WhatsApp.

---

## Support

- Repo issues: https://github.com/virajwathsalag/hermes-Whatsapp-Agent/issues
- Hermes docs: https://hermes-agent.nousresearch.com/docs
