from langchain_core.language_models import BaseChatModel
from core.config import settings


def get_llm() -> BaseChatModel:
    provider = settings.AI_PROVIDER.lower()

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            base_url=settings.OLLAMA_BASE_URL,
            model=settings.OLLAMA_MODEL,
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            api_key=settings.OPENAI_API_KEY,
            model=settings.OPENAI_MODEL,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            model_name=settings.ANTHROPIC_MODEL,
        )

    # xAI (Grok) exposes an OpenAI-compatible endpoint
    if provider == "xai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            api_key=settings.XAI_API_KEY,
            model=settings.XAI_MODEL,
            base_url=settings.XAI_BASE_URL,
        )

    # QWEN (Alibaba) exposes an OpenAI-compatible endpoint
    if provider == "qwen":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            api_key=settings.QWEN_API_KEY,
            model=settings.QWEN_MODEL,
            base_url=settings.QWEN_BASE_URL,
        )
    if provider == "librechat":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            api_key=settings.LIBRECHAT_API_KEY,
            model=settings.LIBRECHAT_MODEL,
            base_url=settings.LIBRECHAT_BASE_URL,
        )

    raise ValueError(
        f"Unsupported AI provider: '{provider}'. "
        "Choose one of: ollama, openai, anthropic, xai, qwen"
    )
