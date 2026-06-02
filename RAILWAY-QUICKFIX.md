# Railway crash fix — do these in order

## Your logs show OLD code

If you see:
```
starting QR pairing wizard
hermes whatsapp requires an interactive terminal
```

Railway is **not** running the latest `entrypoint.sh`. You must **push to GitHub** and redeploy.

After fix, logs must show:
```
==> entrypoint v2.2 (session restore from env, no QR wizard)
```

---

## Step 1 — Regenerate small session file

```powershell
cd E:\Freelancer\Akeel\Local-Setup\hermes\docker\railway
.\scripts\encode-local-session.ps1
```

Must say: `Base64 length: 2720 characters` (NOT 96000+)

---

## Step 2 — Push latest code to GitHub

```powershell
cd E:\Freelancer\Akeel\Local-Setup\hermes\docker\railway
git add entrypoint.sh scripts/ encode-local-session.ps1
git commit -m "Railway v2.2: fix session restore, no QR wizard crash"
git push origin main
```

Wait for Railway to rebuild (2–5 min).

---

## Step 3 — Railway Variables

| Variable | Value |
|----------|-------|
| `WHATSAPP_SESSION_B64` | Paste **entire** `whatsapp-session.b64.txt` (~2720 chars) |
| `MINIMAX_API_KEY` | your key |
| `AIRTABLE_PAT` | your pat |
| Postgres vars | as before |

Delete any old truncated `WHATSAPP_SESSION_B64` that failed the 32KB limit.

---

## Step 4 — Volume mount

Mount path **must** be:

```
/app/.hermes/platforms/whatsapp/session
```

---

## Step 5 — Redeploy and verify logs

Look for:

```
==> entrypoint v2.2
==> Restoring session from base64 (len=2720)
==> Extracted session to /app/.hermes/platforms/whatsapp/session
==> creds.json OK — starting gateway
```

---

## Still failing?

Upload `whatsapp-session.b64.txt` to a public URL (GitHub gist raw, transfer.sh) and set:

```
WHATSAPP_SESSION_URL=https://....../whatsapp-session.b64.txt
```

(Raw file must be the base64 text, not the .tar.gz)

---

## Optional: remove volume temporarily

If volume is empty and blocks restore:

1. Detach volume from service
2. Redeploy once (session written to container disk)
3. Re-attach volume
4. Redeploy again

Usually not needed if `WHATSAPP_SESSION_B64` is set correctly.
