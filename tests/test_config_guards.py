"""
Regression tests for the production config guards in core.config.

Settings._apply_fallbacks_and_guards raises RuntimeError at construction
time when DEBUG=false and any of SECRET_KEY / APP_DATABASE_URL /
CORS_ALLOW_ORIGINS / ALLOW_UNAUTHENTICATED_QUERY are misconfigured for
production. These tests build fresh Settings instances (never mutating the
global `settings` singleton) to verify each guard fires, that dev (DEBUG=true)
stays zero-config, and that APP_DATABASE_URL no longer falls back to
DATABASE_URL.
"""

import pytest
from pydantic import SecretStr

from core.config import Settings

_GUARDED_ENV_VARS = (
    "DEBUG",
    "ALLOW_UNAUTHENTICATED_QUERY",
    "SECRET_KEY",
    "APP_DATABASE_URL",
    "DATABASE_URL",
    "CORS_ALLOW_ORIGINS",
)


def _clear_guarded_env(monkeypatch):
    """conftest.py sets some of these as process-env defaults so Settings()
    can import cleanly at collection time; clear them here so each test
    controls its own env precisely."""
    for var in _GUARDED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class TestProdGuards:
    def test_allow_unauthenticated_query_raises_in_prod(self, monkeypatch):
        _clear_guarded_env(monkeypatch)
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("ALLOW_UNAUTHENTICATED_QUERY", "true")
        monkeypatch.setenv("SECRET_KEY", "a-strong-non-placeholder-secret")
        monkeypatch.setenv("APP_DATABASE_URL", "postgres://app")
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://example.com")

        with pytest.raises(RuntimeError, match="ALLOW_UNAUTHENTICATED_QUERY"):
            Settings(_env_file=None)

    def test_missing_app_database_url_raises_in_prod(self, monkeypatch):
        _clear_guarded_env(monkeypatch)
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("SECRET_KEY", "a-strong-non-placeholder-secret")
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://example.com")
        monkeypatch.delenv("APP_DATABASE_URL", raising=False)

        with pytest.raises(RuntimeError, match="APP_DATABASE_URL"):
            Settings(_env_file=None)

    def test_wildcard_cors_raises_in_prod(self, monkeypatch):
        _clear_guarded_env(monkeypatch)
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("SECRET_KEY", "a-strong-non-placeholder-secret")
        monkeypatch.setenv("APP_DATABASE_URL", "postgres://app")
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", "*")

        with pytest.raises(RuntimeError, match="CORS_ALLOW_ORIGINS"):
            Settings(_env_file=None)

    def test_prod_happy_path_constructs_without_raising(self, monkeypatch):
        _clear_guarded_env(monkeypatch)
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("SECRET_KEY", "a-strong-non-placeholder-secret")
        monkeypatch.setenv("APP_DATABASE_URL", "postgres://app")
        monkeypatch.setenv("DATABASE_URL", "postgres://erp")
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://example.com")

        settings = Settings(_env_file=None)

        assert settings.APP_DATABASE_URL == "postgres://app"
        assert settings.cors_allow_origins == ["https://example.com"]

    def test_wildcard_cors_in_multi_origin_list_raises(self, monkeypatch):
        _clear_guarded_env(monkeypatch)
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("SECRET_KEY", "a-strong-non-placeholder-secret")
        monkeypatch.setenv("APP_DATABASE_URL", "postgres://app")
        monkeypatch.setenv("DATABASE_URL", "postgres://erp")
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://app.example.com,*")

        with pytest.raises(RuntimeError, match="CORS_ALLOW_ORIGINS"):
            Settings(_env_file=None)

    def test_app_database_url_equal_to_database_url_raises(self, monkeypatch):
        _clear_guarded_env(monkeypatch)
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("SECRET_KEY", "a-strong-non-placeholder-secret")
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://example.com")
        monkeypatch.setenv("APP_DATABASE_URL", "postgres://same-db")
        monkeypatch.setenv("DATABASE_URL", "postgres://same-db")

        with pytest.raises(RuntimeError, match="APP_DATABASE_URL must not equal DATABASE_URL"):
            Settings(_env_file=None)


class TestDevFallbacks:
    def test_dev_mode_constructs_with_everything_unset(self, monkeypatch):
        _clear_guarded_env(monkeypatch)
        monkeypatch.setenv("DEBUG", "true")

        settings = Settings(_env_file=None)

        assert settings.APP_DATABASE_URL == "sqlite:///./data/app.db"

    def test_app_database_url_no_longer_falls_back_to_database_url(self, monkeypatch):
        _clear_guarded_env(monkeypatch)
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.setenv("DATABASE_URL", "postgres://erp")
        monkeypatch.delenv("APP_DATABASE_URL", raising=False)

        settings = Settings(_env_file=None)

        assert settings.DATABASE_URL == "postgres://erp"
        assert settings.APP_DATABASE_URL != settings.DATABASE_URL
        assert settings.APP_DATABASE_URL == "sqlite:///./data/app.db"


class TestSecretFields:
    """SECRET_KEY and the provider API keys are SecretStr — verifies they
    round-trip correctly through construction and the dev-fallback path, and
    that validate_assignment doesn't let a plain-str assignment silently
    defeat the SecretStr annotation (the same class of bug the
    cors_allow_origins/included_tables properties guard against)."""

    def test_secret_fields_are_secretstr_and_dont_leak_via_repr(self, monkeypatch):
        _clear_guarded_env(monkeypatch)
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret-value")

        settings = Settings(_env_file=None)

        assert isinstance(settings.OPENAI_API_KEY, SecretStr)
        assert isinstance(settings.SECRET_KEY, SecretStr)
        assert settings.OPENAI_API_KEY.get_secret_value() == "sk-super-secret-value"
        assert "sk-super-secret-value" not in repr(settings.OPENAI_API_KEY)
        assert "sk-super-secret-value" not in str(settings.OPENAI_API_KEY)

    def test_secret_key_dev_fallback_is_coerced_to_secretstr(self, monkeypatch):
        _clear_guarded_env(monkeypatch)
        monkeypatch.setenv("DEBUG", "true")

        settings = Settings(_env_file=None)

        # The fallback assigns a plain str (secrets.token_hex(32)) — with
        # validate_assignment=True it must come out the other side as a
        # SecretStr, not a bare string that would break every
        # .get_secret_value() call site.
        assert isinstance(settings.SECRET_KEY, SecretStr)
        assert len(settings.SECRET_KEY.get_secret_value()) == 64

    def test_empty_secret_key_is_falsy_for_the_fallback_check(self, monkeypatch):
        _clear_guarded_env(monkeypatch)
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.delenv("SECRET_KEY", raising=False)

        settings = Settings(_env_file=None)

        # Guards against SecretStr("") accidentally being truthy, which would
        # skip the token_hex(32) fallback and leave SECRET_KEY empty.
        assert settings.SECRET_KEY.get_secret_value() != ""
