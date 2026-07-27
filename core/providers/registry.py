"""Provider registry — maps an AI_PROVIDER name to a builder callable so adding
a provider is a new `@register` in factory.py rather than an edit to a growing
if/elif chain (OCP)."""

from collections.abc import Callable

from langchain_core.language_models import BaseChatModel

LLMBuilder = Callable[[], BaseChatModel]

_BUILDERS: dict[str, LLMBuilder] = {}


def register(name: str) -> Callable[[LLMBuilder], LLMBuilder]:
    """Decorator: register a builder under a (lower-cased) provider name."""

    def deco(fn: LLMBuilder) -> LLMBuilder:
        _BUILDERS[name.lower()] = fn
        return fn

    return deco


def build(provider: str) -> BaseChatModel:
    """Instantiate the LLM for `provider` (case-insensitive). Raises ValueError
    listing the supported providers when the name is unknown."""
    key = provider.lower()
    try:
        builder = _BUILDERS[key]
    except KeyError:
        raise ValueError(
            f"Unsupported AI provider: '{key}'. Choose one of: {', '.join(sorted(_BUILDERS))}"
        ) from None
    return builder()
