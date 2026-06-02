# Xenko WhatsApp Agent

WhatsApp-powered sales agent for Xenko marketing agency. Built on Hermes Agent, deployed to Railway.

## What's Inside

- **Hermes Agent** — AI sales agent framework
- **WhatsApp Gateway** — Baileys-based WhatsApp connection
- **Airtable CRM** — Lead logging and management
- **5-Step Qualification** — Structured intake flow

## Quick Deploy to Railway

### 1. Create Railway Project

```bash
# Or create via https://railway.new
railway init
```

Add these services:
- **PostgreSQL** — for GBrain memory
- **Redis** — for message queue
- **Empty Service** — your app

### 2. Connect GitHub

Push this repo to GitHub, then:
1. Go to Railway → Your Service → Settings
2. Connect to your GitHub repo
3. Set branch to `main`

### 3. Set Environment Variables

| Variable | Value |
|----------|-------|
| `AIRTABLE_PAT` | Your Airtable PAT (personal access token) |
| `OPENAI_API_KEY` | Your OpenAI API key |
| `HERMES_HOME` | `/app/.hermes` |
| `POSTGRES_HOST` | Your Railway PostgreSQL host |
| `POSTGRES_PORT` | Your Railway PostgreSQL port |
| `POSTGRES_DB` | `railway` |
| `POSTGRES_USER` | `postgres` |
| `POSTGRES_PASSWORD` | Your PostgreSQL password |

### 4. Add WhatsApp Volume

In Railway:
1. Service → Volumes → Create New
2. Name: `whatsapp-sessions`
3. Mount to: `/app/whatsapp-sessions`

This keeps WhatsApp session alive across restarts.

### 5. Deploy

Push to `main` branch → Railway auto-deploys.

### 6. Connect WhatsApp (First Time)

After first deploy:
1. Check Railway logs for QR code
2. Scan with WhatsApp
3. Session saves automatically to volume

## Local Development

```bash
# Build
cd docker/railway
docker build -t xenko-whatsapp .

# Run locally (需要 WhatsApp session dir)
docker run -p 3000:3000 \
  -v ./whatsapp-sessions:/app/whatsapp-sessions \
  -e AIRTABLE_PAT=your_pat \
  -e OPENAI_API_KEY=your_key \
  xenko-whatsapp
```

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  WhatsApp   │────▶│  Hermes      │────▶│  Airtable   │
│  Clients    │     │  Gateway     │     │  CRM        │
└─────────────┘     └──────────────┘     └─────────────┘
                          │
                    ┌─────┴─────┐
                    │ PostgreSQL │
                    │  (Memory) │
                    └───────────┘
```

## Support

- Telegram: @alexrivera
- WhatsApp: Direct message the deployed number

## License

MIT