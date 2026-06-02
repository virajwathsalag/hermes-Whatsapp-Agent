# Xenko WhatsApp Agent

WhatsApp-powered sales agent for **Xenko** marketing agency. Built on [Hermes Agent](https://hermes-agent.nousresearch.com), deployed to Railway.

## Features

- **Hermes Gateway** — AI sales agent with MiniMax M2.1
- **WhatsApp (Baileys)** — Direct WhatsApp connection via QR scan
- **Airtable CRM** — Automatic lead capture after 5-step intake
- **Intake Guard** — Enforces qualification flow, blocks premature closes
- **GBrain Memory** — PostgreSQL-backed conversation history

## Quick Start

**Full deployment guide:** [DEPLOY.md](./DEPLOY.md)

```bash
# 1. Clone / push to GitHub
git clone https://github.com/virajwathsalag/hermes-Whatsapp-Agent.git
cd hermes-Whatsapp-Agent

# 2. Deploy on Railway
#    - Connect GitHub repo
#    - Add PostgreSQL database
#    - Set variables from .env.example
#    - Add volumes: /app/whatsapp-sessions + /app/.hermes
#    - Scan WhatsApp QR in deploy logs

# 3. Test
curl https://YOUR-APP.up.railway.app/health
```

## Environment Variables

Copy `.env.example` to Railway Dashboard → Variables. Required:

| Variable | Description |
|----------|-------------|
| `MINIMAX_API_KEY` | LLM provider key |
| `AIRTABLE_PAT` | Airtable personal access token |
| `POSTGRES_*` | From Railway PostgreSQL plugin |
| `WHATSAPP_SESSION_DIR` | `/app/whatsapp-sessions` |
| `HERMES_PRIVILEGED_PHONE` | Owner phone for full tool access |

## Architecture

```
WhatsApp → Hermes Gateway → Airtable CRM
                ↓
           PostgreSQL (GBrain)
```

## License

MIT
