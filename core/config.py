import os
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
    # Use a strong random value in production: openssl rand -hex 32
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")

    # Debug — enables verbose agent logging and unauthenticated /query access.
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    ALLOW_UNAUTHENTICATED_QUERY: bool = os.getenv("ALLOW_UNAUTHENTICATED_QUERY", "false").lower() == "true"


settings = Settings()
