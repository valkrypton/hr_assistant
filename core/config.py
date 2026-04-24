import os
import secrets
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # AI Provider: ollama | openai | anthropic | xai | qwen | librechat
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "ollama")

    # Ollama
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2")

    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")

    # Anthropic
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    # xAI (Grok) — uses OpenAI-compatible API
    XAI_API_KEY: str = os.getenv("XAI_API_KEY", "")
    XAI_MODEL: str = os.getenv("XAI_MODEL", "grok-beta")
    XAI_BASE_URL: str = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")

    # QWEN (Alibaba) — uses OpenAI-compatible API
    QWEN_API_KEY: str = os.getenv("QWEN_API_KEY", "")
    QWEN_MODEL: str = os.getenv("QWEN_MODEL", "qwen-max")
    QWEN_BASE_URL: str = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

    LIBRECHAT_API_KEY: str = os.getenv("LIBRECHAT_API_KEY", "")
    LIBRECHAT_MODEL: str = os.getenv("LIBRECHAT_MODEL", "xai/grok-4-0709")
    LIBRECHAT_BASE_URL: str = os.getenv("LIBRECHAT_BASE_URL", "https://litellm.arbisoft.com")

    # Trusted proxy/load-balancer hosts for X-Forwarded-* headers.
    # Restrict to actual proxy addresses in production.
    TRUSTED_PROXY_HOSTS: str = os.getenv("TRUSTED_PROXY_HOSTS", "127.0.0.1")

    # ERP database — read-only; used exclusively by the SQL agent.
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./data/company.db")

    # App database — writable; stores hr_assistant_users, audit logs, etc.
    # Defaults to the same DB as DATABASE_URL for local dev convenience.
    APP_DATABASE_URL: str = os.getenv("APP_DATABASE_URL", os.getenv("DATABASE_URL", "sqlite:///./data/app.db"))

    # Whitelist: only these tables are visible to the agent.
    # All other tables in the database are invisible to the agent.
    INCLUDED_TABLES: list[str] = [
        t.strip() for t in os.getenv("INCLUDED_TABLES", "").split(",") if t.strip()
    ]

    # Slack integration (Phase 3)
    SLACK_BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")
    SLACK_SIGNING_SECRET: str = os.getenv("SLACK_SIGNING_SECRET", "")

    # Rate limiting — max queries per user per hour. Set to 0 to disable.
    RATE_LIMIT_PER_HOUR: int = int(os.getenv("RATE_LIMIT_PER_HOUR", "30"))

    # Vector index
    VECTOR_STORE_PATH: str = os.getenv("VECTOR_STORE_PATH", "./data/chroma")
    VECTOR_EMBEDDING_MODEL: str = os.getenv("VECTOR_EMBEDDING_MODEL", "nomic-embed-text")

    # Secret key for signing admin session cookies (SQLAdmin panel).
    # Set SECRET_KEY in the environment for production; openssl rand -hex 32
    # When unset, a random key is generated — sessions won't survive restarts
    # or multi-worker deploys.
    SECRET_KEY: str = os.getenv("SECRET_KEY") or secrets.token_hex(32)

    # Debug — enables verbose agent logging and unauthenticated /query access.
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    ALLOW_UNAUTHENTICATED_QUERY: bool = os.getenv("ALLOW_UNAUTHENTICATED_QUERY", "false").lower() == "true"


settings = Settings()

_WEAK_SECRET_PLACEHOLDERS = {"change-me-in-production", "changeme", "secret"}

if not settings.DEBUG:
    _raw_secret_key = os.getenv("SECRET_KEY", "")
    if not _raw_secret_key:
        raise RuntimeError(
            "SECRET_KEY is not set. Each worker process will use a different "
            "random key, breaking admin sessions across restarts or workers. "
            "Run: export SECRET_KEY=$(openssl rand -hex 32)"
        )
    if _raw_secret_key in _WEAK_SECRET_PLACEHOLDERS:
        raise RuntimeError(
            "SECRET_KEY uses a known-weak placeholder value. "
            "Run: export SECRET_KEY=$(openssl rand -hex 32)"
        )
