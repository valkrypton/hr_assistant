# Deployment

The repo ships with a `Dockerfile` and a `railway.toml` for deploying to [Railway](https://railway.app) (or any Docker host).

## Docker image (`Dockerfile`)

What the image actually does:

- Base: `python:3.13-slim`.
- Installs `build-essential` via apt. This was originally added for chromadb's compiled extensions; chromadb has since been removed, so this layer is a candidate for future cleanup — verify with a full `docker build` that no remaining dependency compiles from source before dropping it.
- Copies the `uv` binary from the official `ghcr.io/astral-sh/uv` image (pinned version).
- Copies `pyproject.toml` + `uv.lock` and runs `uv sync --frozen --no-dev` — production deps only, exactly as locked.
- Copies the repo (filtered by `.dockerignore` — `.env`, `.venv`, tests, and most Markdown are excluded; `core/context/schema.md` is explicitly kept because the agent injects it at runtime).
- Exposes port 8000 and starts `uv run uvicorn api.main:app --host 0.0.0.0 --port ${PORT}`. Railway injects `$PORT`; it defaults to 8000 for local `docker run`.

Build and run locally:

```bash
docker build -t hr-assistant .
docker run --rm -p 8000:8000 --env-file .env hr-assistant
```

## Railway (`railway.toml`)

- `builder = "dockerfile"` — Railway builds the image from the repo's `Dockerfile`.
- `healthcheckPath = "/health"` with a 300 s timeout — a deploy is only marked live once `GET /health` returns 200 (both databases reachable).
- `restartPolicyType = "on_failure"` — crashed containers are restarted automatically.

## Production checklist

The app **fails fast at startup** when `DEBUG=false` (the default) and any of these is misconfigured — the guards live in `core/config.py` (`_apply_fallbacks_and_guards`):

- [ ] `SECRET_KEY` is set (and not a placeholder like `change-me-in-production`). Generate with `openssl rand -hex 32`. Without it, admin sessions break across restarts/workers.
- [ ] `APP_DATABASE_URL` is set explicitly (there is no fallback to `DATABASE_URL`, and it must **not equal** `DATABASE_URL` — the writable app DB must be a separate connection from the read-only ERP).
- [ ] Nothing to configure for `/query` auth — it **always** requires admin HTTP Basic Auth and cannot be disabled (the former dev-only escape-hatch env var was removed by product decision).
- [ ] `CORS_ALLOW_ORIGINS` contains no `*` — set an explicit comma-separated list of frontend origins.

## Environment variables to set

| Variable | Notes |
|---|---|
| `DATABASE_URL` | Read-only ERP PostgreSQL connection |
| `APP_DATABASE_URL` | Writable app DB (users, admins) — must differ from `DATABASE_URL` |
| `AI_PROVIDER` + provider keys | e.g. `openai` + `OPENAI_API_KEY`/`OPENAI_MODEL` — see README "LLM Providers" |
| `INCLUDED_TABLES` | Whitelist of ERP tables the agent may see |
| `SECRET_KEY` | Admin session-cookie signing — `openssl rand -hex 32` |
| `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET` | Slack integration |
| `CORS_ALLOW_ORIGINS` | Explicit frontend origins (no `*` in prod) |
| `TRUSTED_PROXY_HOSTS` | Your proxy/load-balancer addresses for `X-Forwarded-*` |
| `DEBUG` | Leave unset/`false` in production |

After the first deploy, run the migrations and create an admin:

```bash
uv run alembic upgrade head
uv run python scripts/create_admin.py <username>
```

## Verify

```bash
curl https://your-server/health
# → {"status":"ok","erp_database":"connected","app_database":"connected"}
```

A 503 means one of the two databases is unreachable — the response detail says which.

## Secrets

Rotation procedures for every credential (DB URLs, provider keys, Slack tokens, `SECRET_KEY`) are documented in [secrets-rotation.md](secrets-rotation.md).
