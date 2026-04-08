import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # AI Provider: ollama | openai | anthropic | xai | qwen
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "ollama")

    # Ollama
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1you ")

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

    LIBRECHAT_API_KEY = os.getenv("LIBRECHAT_API_KEY", "")
    LIBRECHAT_MODEL = os.getenv("LIBRECHAT_MODEL", "xai/grok-4-0709")
    LIBRECHAT_BASE_URL = os.getenv("LIBRECHAT_BASE_URL", "https://litellm.arbisoft.com")

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./data/company.db")

    # Whitelist: only these tables are visible to the agent.
    # All other tables in the database are invisible to the agent.
    INCLUDED_TABLES: list[str] = [
        t.strip() for t in os.getenv("INCLUDED_TABLES", "").split(",") if t.strip()
    ]

    # Vector index
    VECTOR_STORE_PATH: str = os.getenv("VECTOR_STORE_PATH", "./data/chroma")
    VECTOR_EMBEDDING_MODEL: str = os.getenv("VECTOR_EMBEDDING_MODEL", "nomic-embed-text")


settings = Settings()
