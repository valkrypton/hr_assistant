import os
import secrets

from dotenv import load_dotenv
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# pydantic-settings' env_file loading only feeds its own internal source, not
# os.environ — load .env into the process environment too so the raw-env
# guard below (os.getenv("SECRET_KEY")) sees values that only live in .env.
load_dotenv()

_WEAK_SECRET_PLACEHOLDERS = {"change-me-in-production", "changeme", "secret"}

# Shared SQLAlchemy engine tuning — every create_engine()/from_uri() call site
# (ERP and app DB, across core/agent.py, api/deps.py, adapters/slack.py) uses
# this so pool tuning can't drift out of sync between them.
DEFAULT_ENGINE_ARGS: dict = {"pool_pre_ping": True, "pool_recycle": 300}


class Settings(BaseSettings):
    # validate_assignment: pydantic doesn't validate in-place attribute
    # mutation by default. Without this, assigning a plain str to a SecretStr
    # field (e.g. the SECRET_KEY dev-fallback below) would silently store a
    # bare string where every call site expects .get_secret_value() to exist —
    # the same class of bug that motivated the cors_allow_origins/
    # included_tables properties above. With it on, such assignments are
    # coerced through the field's validator like construction-time input is.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", validate_assignment=True
    )

    # AI Provider: ollama | openai | anthropic | xai | qwen | librechat
    AI_PROVIDER: str = "ollama"

    # Ollama
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2"

    # OpenAI
    OPENAI_API_KEY: SecretStr = SecretStr("")
    OPENAI_MODEL: str = "gpt-4o"

    # Anthropic
    ANTHROPIC_API_KEY: SecretStr = SecretStr("")
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"

    # xAI (Grok) — uses OpenAI-compatible API
    XAI_API_KEY: SecretStr = SecretStr("")
    XAI_MODEL: str = "grok-beta"
    XAI_BASE_URL: str = "https://api.x.ai/v1"

    # QWEN (Alibaba) — uses OpenAI-compatible API
    QWEN_API_KEY: SecretStr = SecretStr("")
    QWEN_MODEL: str = "qwen-max"
    QWEN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    LIBRECHAT_API_KEY: SecretStr = SecretStr("")
    LIBRECHAT_MODEL: str = "xai/grok-4-0709"
    LIBRECHAT_BASE_URL: str = "https://litellm.arbisoft.com"

    # Trusted proxy/load-balancer hosts for X-Forwarded-* headers.
    # Restrict to actual proxy addresses in production.
    TRUSTED_PROXY_HOSTS: str = "127.0.0.1"

    # CORS — comma-separated browser origins allowed to call the API.
    # "*" is fine for local dev (index.html); set to the real frontend
    # origin(s) in production.
    # Declared as `str` (not `list[str]`) so pydantic-settings doesn't attempt
    # to JSON-decode the env value — the raw string is parsed into a list by
    # the cors_allow_origins property below, which every call site uses
    # instead of this field directly.
    CORS_ALLOW_ORIGINS: str = "*"

    # ERP database — read-only; used exclusively by the SQL agent.
    DATABASE_URL: str = ""

    # App database — writable; stores hr_assistant_users, hr_admin_users.
    # Required — no fallback to DATABASE_URL.
    APP_DATABASE_URL: str = ""

    # Whitelist: only these tables are visible to the agent. Raw comma-separated
    # string — parsed into a list by the included_tables property below.
    # All other tables in the database are invisible to the agent.
    INCLUDED_TABLES: str = ""

    # Pool sizing for the shared ERP engine (core/agent.py _erp_db). That
    # engine is now a single process-wide cached instance instead of one
    # built fresh per restricted-role request, so this caps total concurrent
    # ERP SQL across every in-flight request — raise via env if bursts start
    # queuing/500ing.
    ERP_POOL_SIZE: int = 5
    ERP_MAX_OVERFLOW: int = 10

    # Slack integration (Phase 3)
    SLACK_BOT_TOKEN: SecretStr = SecretStr("")
    SLACK_SIGNING_SECRET: SecretStr = SecretStr("")

    # Secret key for signing admin session cookies (SQLAdmin panel).
    # Set SECRET_KEY in the environment for production; openssl rand -hex 32
    # When unset, a random key is generated — sessions won't survive restarts
    # or multi-worker deploys.
    SECRET_KEY: SecretStr = SecretStr("")

    # Debug — enables verbose agent logging and unauthenticated /query access.
    DEBUG: bool = False
    ALLOW_UNAUTHENTICATED_QUERY: bool = False

    @field_validator("DEBUG", "ALLOW_UNAUTHENTICATED_QUERY", mode="before")
    @classmethod
    def _strict_bool_from_str(cls, v):
        """Only the literal string "true" (case-insensitive) is True — matches
        the previous os.getenv(...).lower() == "true" parser. Pydantic's
        default bool coercion also accepts "1"/"yes"/"on"/"y"/"t", which would
        silently widen what counts as enabled here — security-relevant for
        ALLOW_UNAUTHENTICATED_QUERY (bypasses /query auth) and the DEBUG-gated
        SECRET_KEY guard below."""
        if isinstance(v, str):
            return v.strip().lower() == "true"
        return v

    @property
    def cors_allow_origins(self) -> list[str]:
        """CORS_ALLOW_ORIGINS parsed into a list — every call site uses this,
        not the raw field, so the `str` annotation above never lies about
        what's stored (pydantic doesn't validate attribute assignment by
        default, so mutating the field in place to a list would silently
        defeat its own type)."""
        return [o.strip() for o in self.CORS_ALLOW_ORIGINS.split(",") if o.strip()]

    @property
    def included_tables(self) -> list[str]:
        """INCLUDED_TABLES parsed into a list — see cors_allow_origins above."""
        return [t.strip() for t in self.INCLUDED_TABLES.split(",") if t.strip()]

    @model_validator(mode="after")
    def _apply_fallbacks_and_guards(self) -> "Settings":
        # DATABASE_URL falls back to the local sqlite ERP db in dev.
        if not self.DATABASE_URL:
            self.DATABASE_URL = "sqlite:///./data/company.db"

        # APP_DATABASE_URL must NOT fall back to DATABASE_URL: pointing the
        # writable app connection at the read-only ERP DB would expose
        # hr_admin_users (password hashes) and the audit log to the SQL agent.
        # In production it must be set explicitly (guard below); in dev it
        # falls back to its own separate sqlite file.
        if not self.APP_DATABASE_URL:
            self.APP_DATABASE_URL = "sqlite:///./data/app.db"

        if not self.SECRET_KEY:
            self.SECRET_KEY = secrets.token_hex(32)

        # Production guards. All keyed on DEBUG=false so local dev keeps its
        # zero-config defaults while a misconfigured prod deploy fails fast at
        # startup instead of silently running insecure. Guards that have a
        # fallback (SECRET_KEY, APP_DATABASE_URL) check the raw env var, not the
        # post-fallback self.* value, so the fallback can't mask a missing setting.
        if not self.DEBUG:
            raw_secret_key = os.getenv("SECRET_KEY", "")
            if not raw_secret_key:
                raise RuntimeError(
                    "SECRET_KEY is not set. Each worker process will use a different "
                    "random key, breaking admin sessions across restarts or workers. "
                    "Run: export SECRET_KEY=$(openssl rand -hex 32)"
                )
            if raw_secret_key in _WEAK_SECRET_PLACEHOLDERS:
                raise RuntimeError(
                    "SECRET_KEY uses a known-weak placeholder value. "
                    "Run: export SECRET_KEY=$(openssl rand -hex 32)"
                )

            # ALLOW_UNAUTHENTICATED_QUERY removes all auth from /query and trusts
            # the request-supplied slack_user_id to select an RBAC scope — a full
            # data breach if enabled with a reachable endpoint. Dev-only.
            if self.ALLOW_UNAUTHENTICATED_QUERY:
                raise RuntimeError(
                    "ALLOW_UNAUTHENTICATED_QUERY=true is not allowed when DEBUG is false: "
                    "it disables /query authentication. Unset it in production."
                )

            # APP_DATABASE_URL must be set explicitly in production (see the
            # no-fallback note above). Silently defaulting to a local sqlite file
            # would split app state per worker and lose it on redeploy.
            if not os.getenv("APP_DATABASE_URL"):
                raise RuntimeError(
                    "APP_DATABASE_URL is not set. It must point at the writable app "
                    "database explicitly in production (it does not fall back to "
                    "DATABASE_URL)."
                )

            # Even when set explicitly, the writable app DB must not be the same
            # connection as the read-only ERP — that would expose hr_admin_users
            # (password hashes) to the SQL agent.
            if os.getenv("APP_DATABASE_URL") == os.getenv("DATABASE_URL"):
                raise RuntimeError(
                    "APP_DATABASE_URL must not equal DATABASE_URL. The writable app "
                    "database must be a separate connection from the read-only ERP."
                )

            # A wildcard CORS origin should never ship to production; require an
            # explicit allowlist. Catch "*" anywhere in the list — Starlette
            # treats a single "*" element as allow-all.
            if "*" in self.cors_allow_origins:
                raise RuntimeError(
                    "CORS_ALLOW_ORIGINS must not contain '*' when DEBUG is false. "
                    "Set an explicit comma-separated list of frontend origins."
                )

        return self


settings = Settings()
