import os
import secrets

from dotenv import load_dotenv
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# pydantic-settings' env_file loading only feeds its own internal source, not
# os.environ — load .env into the process environment too so the raw-env
# guard below (os.getenv("SECRET_KEY")) sees values that only live in .env.
load_dotenv()

_WEAK_SECRET_PLACEHOLDERS = {"change-me-in-production", "changeme", "secret"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # AI Provider: ollama | openai | anthropic | xai | qwen | librechat
    AI_PROVIDER: str = "ollama"

    # Ollama
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2"

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"

    # Anthropic
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"

    # xAI (Grok) — uses OpenAI-compatible API
    XAI_API_KEY: str = ""
    XAI_MODEL: str = "grok-beta"
    XAI_BASE_URL: str = "https://api.x.ai/v1"

    # QWEN (Alibaba) — uses OpenAI-compatible API
    QWEN_API_KEY: str = ""
    QWEN_MODEL: str = "qwen-max"
    QWEN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    LIBRECHAT_API_KEY: str = ""
    LIBRECHAT_MODEL: str = "xai/grok-4-0709"
    LIBRECHAT_BASE_URL: str = "https://litellm.arbisoft.com"

    # Trusted proxy/load-balancer hosts for X-Forwarded-* headers.
    # Restrict to actual proxy addresses in production.
    TRUSTED_PROXY_HOSTS: str = "127.0.0.1"

    # CORS — comma-separated browser origins allowed to call the API.
    # "*" is fine for local dev (index.html); set to the real frontend
    # origin(s) in production.
    # Declared as `str` (not `list[str]`) so pydantic-settings doesn't attempt
    # to JSON-decode the env value — comma-splitting happens in
    # _parse_comma_separated below, same as the previous plain-class behavior.
    CORS_ALLOW_ORIGINS: str = "*"

    # ERP database — read-only; used exclusively by the SQL agent.
    DATABASE_URL: str = ""

    # App database — writable; stores hr_assistant_users, audit logs, etc.
    # Required — no fallback to DATABASE_URL.
    APP_DATABASE_URL: str = ""

    # Whitelist: only these tables are visible to the agent.
    # All other tables in the database are invisible to the agent.
    INCLUDED_TABLES: str = ""

    # Slack integration (Phase 3)
    SLACK_BOT_TOKEN: str = ""
    SLACK_SIGNING_SECRET: str = ""

    # Rate limiting — max queries per user per hour. Set to 0 to disable.
    RATE_LIMIT_PER_HOUR: int = 30

    # Vector index
    VECTOR_STORE_PATH: str = "./data/chroma"
    VECTOR_EMBEDDING_MODEL: str = "nomic-embed-text"

    # Secret key for signing admin session cookies (SQLAdmin panel).
    # Set SECRET_KEY in the environment for production; openssl rand -hex 32
    # When unset, a random key is generated — sessions won't survive restarts
    # or multi-worker deploys.
    SECRET_KEY: str = ""

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

    @model_validator(mode="after")
    def _apply_fallbacks_and_guards(self) -> "Settings":
        # CORS_ALLOW_ORIGINS / INCLUDED_TABLES arrive as comma-separated strings;
        # split them here (once, at construction) into the list[str] shape every
        # call site expects.
        self.CORS_ALLOW_ORIGINS = [
            o.strip() for o in self.CORS_ALLOW_ORIGINS.split(",") if o.strip()
        ]
        self.INCLUDED_TABLES = [
            t.strip() for t in self.INCLUDED_TABLES.split(",") if t.strip()
        ]

        # Preserve the pre-pydantic-settings defaults: DATABASE_URL falls back
        # to the local sqlite ERP db, and APP_DATABASE_URL falls back to the
        # raw DATABASE_URL env var (if set) before its own sqlite default —
        # matching the old os.getenv("APP_DATABASE_URL", os.getenv("DATABASE_URL", ...)) chain.
        if not self.DATABASE_URL:
            self.DATABASE_URL = "sqlite:///./data/company.db"

        if not self.APP_DATABASE_URL:
            self.APP_DATABASE_URL = os.getenv("DATABASE_URL") or "sqlite:///./data/app.db"

        if not self.SECRET_KEY:
            self.SECRET_KEY = secrets.token_hex(32)

        # Fail fast on a missing/weak SECRET_KEY in production. Checked against
        # the raw env var (not self.SECRET_KEY, which has already fallen back
        # to a random value above) so the random-fallback case is still caught.
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

        return self


settings = Settings()
