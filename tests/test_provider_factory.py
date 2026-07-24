"""
Coverage for core/providers/factory.py — previously untested (every branch
unexercised, per the architecture review's Testing gaps).

Each branch is exercised for real (no mocking of the Chat* classes): LangChain
chat model constructors only store config and don't make a network call, so
this is safe and verifies the actual integration — right class, right model/
base_url wiring — rather than just that some mock was called.
"""

import pytest
from pydantic import SecretStr

from core.config import settings
from core.providers.factory import get_llm


class TestGetLLM:
    def test_ollama_provider(self, monkeypatch):
        from langchain_ollama import ChatOllama

        monkeypatch.setattr(settings, "AI_PROVIDER", "ollama")

        llm = get_llm()

        assert isinstance(llm, ChatOllama)
        assert llm.model == settings.OLLAMA_MODEL

    def test_openai_provider(self, monkeypatch):
        from langchain_openai import ChatOpenAI

        monkeypatch.setattr(settings, "AI_PROVIDER", "openai")
        monkeypatch.setattr(settings, "OPENAI_API_KEY", SecretStr("sk-test"))

        llm = get_llm()

        assert isinstance(llm, ChatOpenAI)
        assert llm.model_name == settings.OPENAI_MODEL

    def test_anthropic_provider(self, monkeypatch):
        from langchain_anthropic import ChatAnthropic

        monkeypatch.setattr(settings, "AI_PROVIDER", "anthropic")
        monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", SecretStr("sk-ant-test"))

        llm = get_llm()

        assert isinstance(llm, ChatAnthropic)
        assert llm.model == settings.ANTHROPIC_MODEL

    def test_xai_provider_uses_openai_compatible_client(self, monkeypatch):
        from langchain_openai import ChatOpenAI

        monkeypatch.setattr(settings, "AI_PROVIDER", "xai")
        monkeypatch.setattr(settings, "XAI_API_KEY", SecretStr("xai-test"))

        llm = get_llm()

        assert isinstance(llm, ChatOpenAI)
        assert llm.model_name == settings.XAI_MODEL
        assert str(llm.openai_api_base) == settings.XAI_BASE_URL

    def test_qwen_provider_uses_openai_compatible_client(self, monkeypatch):
        from langchain_openai import ChatOpenAI

        monkeypatch.setattr(settings, "AI_PROVIDER", "qwen")
        monkeypatch.setattr(settings, "QWEN_API_KEY", SecretStr("qwen-test"))

        llm = get_llm()

        assert isinstance(llm, ChatOpenAI)
        assert llm.model_name == settings.QWEN_MODEL
        assert str(llm.openai_api_base) == settings.QWEN_BASE_URL

    def test_librechat_provider_uses_openai_compatible_client(self, monkeypatch):
        from langchain_openai import ChatOpenAI

        monkeypatch.setattr(settings, "AI_PROVIDER", "librechat")
        monkeypatch.setattr(settings, "LIBRECHAT_API_KEY", SecretStr("librechat-test"))

        llm = get_llm()

        assert isinstance(llm, ChatOpenAI)
        assert llm.model_name == settings.LIBRECHAT_MODEL
        assert str(llm.openai_api_base) == settings.LIBRECHAT_BASE_URL

    def test_provider_name_is_case_insensitive(self, monkeypatch):
        from langchain_ollama import ChatOllama

        monkeypatch.setattr(settings, "AI_PROVIDER", "OLLAMA")

        assert isinstance(get_llm(), ChatOllama)

    def test_unsupported_provider_raises_value_error(self, monkeypatch):
        monkeypatch.setattr(settings, "AI_PROVIDER", "not-a-real-provider")

        with pytest.raises(ValueError, match="Unsupported AI provider"):
            get_llm()
