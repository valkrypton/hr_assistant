from langchain_core.language_models import BaseChatModel

from core.config import settings
from core.providers.registry import build, register


def _openai_compatible(api_key: str, model: str, base_url: str | None = None) -> BaseChatModel:
    """Shared builder for OpenAI and the OpenAI-compatible endpoints (xAI, QWEN,
    LibreChat). Lazy import so only the selected provider's package is loaded."""
    from langchain_openai import ChatOpenAI

    if base_url is None:
        return ChatOpenAI(api_key=api_key, model=model)
    return ChatOpenAI(api_key=api_key, model=model, base_url=base_url)


@register("ollama")
def _ollama() -> BaseChatModel:
    from langchain_ollama import ChatOllama

    return ChatOllama(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.OLLAMA_MODEL,
    )


@register("openai")
def _openai() -> BaseChatModel:
    return _openai_compatible(settings.OPENAI_API_KEY.get_secret_value(), settings.OPENAI_MODEL)


@register("anthropic")
def _anthropic() -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        api_key=settings.ANTHROPIC_API_KEY.get_secret_value(),
        model_name=settings.ANTHROPIC_MODEL,
    )


# xAI (Grok) exposes an OpenAI-compatible endpoint
@register("xai")
def _xai() -> BaseChatModel:
    return _openai_compatible(
        settings.XAI_API_KEY.get_secret_value(),
        settings.XAI_MODEL,
        settings.XAI_BASE_URL,
    )


# QWEN (Alibaba) exposes an OpenAI-compatible endpoint
@register("qwen")
def _qwen() -> BaseChatModel:
    return _openai_compatible(
        settings.QWEN_API_KEY.get_secret_value(),
        settings.QWEN_MODEL,
        settings.QWEN_BASE_URL,
    )


@register("librechat")
def _librechat() -> BaseChatModel:
    return _openai_compatible(
        settings.LIBRECHAT_API_KEY.get_secret_value(),
        settings.LIBRECHAT_MODEL,
        settings.LIBRECHAT_BASE_URL,
    )


def get_llm() -> BaseChatModel:
    return build(settings.AI_PROVIDER)
