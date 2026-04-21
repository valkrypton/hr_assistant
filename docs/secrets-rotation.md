# Secrets Rotation Guide

How to rotate credentials for HR Assistant without downtime.

---

## General Checklist

After rotating any secret:
1. Update the value in `.env`
2. Restart the server: `uvicorn api.main:app --reload` (or restart the process manager)
3. Verify: `GET /health` should return `{"status": "ok"}`
4. Send a test query and confirm a response
5. Check the audit log at `/admin` for any errors

---

## Slack Bot Token (`SLACK_BOT_TOKEN`)

**When to rotate:** Token compromised, bot removed and re-added, or periodic security policy.

**Downtime:** None — new token takes effect on restart.

1. Go to **https://api.slack.com/apps** → your app → **OAuth & Permissions**
2. Click **Revoke Token** next to the current Bot User OAuth Token
3. Click **Reinstall to Workspace** → Allow
4. Copy the new **Bot User OAuth Token** (`xoxb-...`)
5. Update `.env`:
   ```
   SLACK_BOT_TOKEN=xoxb-new-token-here
   ```
6. Restart the server
7. Send `@HR Assistant test` in `#ask-hr` to confirm the bot responds

---

## Slack Signing Secret (`SLACK_SIGNING_SECRET`)

**When to rotate:** Secret compromised or requested by security team.

**Downtime:** ~1–2 seconds while the server restarts. Events sent by Slack during that window will fail signature verification and be dropped (Slack will retry them).

1. Go to **https://api.slack.com/apps** → your app → **Basic Information**
2. Under **App Credentials**, click **Regenerate** next to Signing Secret
3. Copy the new secret immediately (it is only shown once)
4. Update `.env`:
   ```
   SLACK_SIGNING_SECRET=new-secret-here
   ```
5. Restart the server as quickly as possible
6. Slack automatically retries failed events — any dropped events will be redelivered within 60 seconds

---

## AI API Keys

All AI providers are called per-request, so rotating the key requires only an `.env` update and restart. No data is lost.

| Provider | Env var | Where to rotate |
|----------|---------|-----------------|
| OpenAI | `OPENAI_API_KEY` | platform.openai.com → API keys |
| Anthropic | `ANTHROPIC_API_KEY` | console.anthropic.com → API keys |
| xAI (Grok) | `XAI_API_KEY` | console.x.ai → API keys |
| QWEN | `QWEN_API_KEY` | dashscope.aliyuncs.com → API keys |

**Steps (same for all providers):**
1. Generate a new key in the provider's dashboard — do **not** delete the old one yet
2. Update `.env` with the new key
3. Restart the server
4. Verify with a test query
5. Delete the old key from the provider's dashboard

Keeping the old key alive until after the restart ensures zero dropped requests.

---

## Database Credentials

**ERP database (`DATABASE_URL`)** — read-only connection used by the SQL agent.

**App database (`APP_DATABASE_URL`)** — writable connection used for users and audit logs.

**Downtime:** None if you follow the steps below. The server holds a connection pool; rotating without draining it will cause errors until restart.

1. In your database (PostgreSQL), create a new user or rotate the password:
   ```sql
   ALTER USER hr_agent_user WITH PASSWORD 'new-password';
   ```
2. Update `.env`:
   ```
   DATABASE_URL=postgresql://hr_agent_user:new-password@host:5432/erp_db
   APP_DATABASE_URL=postgresql://hr_agent_user:new-password@host:5432/hr_assistant_db
   ```
3. Restart the server — existing connections are dropped and rebuilt with the new credentials
4. Run `GET /health` to confirm both databases reconnect successfully

> If you are rotating because of a breach, revoke the old credentials in the database **before** restarting to prevent the compromised credentials from being used during the restart window.

---

## Full Rotation (all secrets at once)

If multiple secrets need to be rotated simultaneously (e.g. after a security incident):

1. Prepare all new values before touching anything
2. Update `.env` with all new values at once
3. Rotate the secrets in their respective dashboards
4. Restart the server once
5. Run the full verification checklist above

Doing it in one restart minimises the downtime window compared to rotating and restarting one at a time.
