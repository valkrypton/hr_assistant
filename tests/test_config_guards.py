"""
Production config guard tests (PR1 hardening).

Covers core.config.Settings._apply_fallbacks_and_guards:
  - DEBUG=false + ALLOW_UNAUTHENTICATED_QUERY=true -> RuntimeError
  - DEBUG=false + APP_DATABASE_URL unset -> RuntimeError
  - DEBUG=false + CORS_ALLOW_ORIGINS="*" -> RuntimeError
  - DEBUG=true (dev) constructs fine and APP_DATABASE_URL defaults to the
    local sqlite file, NOT to DATABASE_URL (the cross-fallback was removed)
  - A fully-configured prod construction succeeds

Each test builds a fresh Settings() via monkeypatched os.environ — the
global `settings` singleton (constructed at import time in core.config) is
never mutated. `_env_file=None` keeps construction hermetic by disabling
the real .env file source, so only monkeypatched env vars are read.

No database or LLM is involved — all pure unit tests.
"""

import pytest

from core.config import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_guarded_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every env var the guards/fallbacks inspect so each test starts
    from a blank slate instead of inheriting conftest.py's import-time
    defaults or the caller's shell environment."""
    for key in (
        "DEBUG",
        "ALLOW_UNAUTHENTICATED_QUERY",
        "SECRET_KEY",
        "APP_DATABASE_URL",
        "DATABASE_URL",
        "CORS_ALLOW_ORIGINS",
    ):
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# Prod guards (DEBUG=false)
# ---------------------------------------------------------------------------


class TestProdGuards:
    def test_allow_unauthenticated_query_true_raises(self, monkeypatch):
        _clear_guarded_env(monkeypatch)
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("SECRET_KEY", "a-strong-non-placeholder-secret")
        monkeypatch.setenv("ALLOW_UNAUTHENTICATED_QUERY", "true")

        with pytest.raises(RuntimeError, match="ALLOW_UNAUTHENTICATED_QUERY"):
            Settings(_env_file=None)

    def test_app_database_url_unset_raises(self, monkeypatch):
        _clear_guarded_env(monkeypatch)
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("SECRET_KEY", "a-strong-non-placeholder-secret")
        monkeypatch.delenv("APP_DATABASE_URL", raising=False)

        with pytest.raises(RuntimeError, match="APP_DATABASE_URL"):
            Settings(_env_file=None)

    def test_cors_wildcard_raises(self, monkeypatch):
        _clear_guarded_env(monkeypatch)
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("SECRET_KEY", "a-strong-non-placeholder-secret")
        monkeypatch.setenv("APP_DATABASE_URL", "postgresql://prod-host/appdb")
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", "*")

        with pytest.raises(RuntimeError, match="CORS_ALLOW_ORIGINS"):
            Settings(_env_file=None)

    def test_fully_configured_prod_construction_succeeds(self, monkeypatch):
        _clear_guarded_env(monkeypatch)
        monkeypatch.setenv("DEBUG", "false")
        monkeypatch.setenv("SECRET_KEY", "a-strong-non-placeholder-secret")
        monkeypatch.setenv("APP_DATABASE_URL", "postgresql://prod-host/appdb")
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://app.example.com")

        s = Settings(_env_file=None)

        assert s.DEBUG is False
        assert s.APP_DATABASE_URL == "postgresql://prod-host/appdb"
        assert s.CORS_ALLOW_ORIGINS == ["https://app.example.com"]


# ---------------------------------------------------------------------------
# Dev mode (DEBUG=true) — guards are inert, fallback behavior verified
# ---------------------------------------------------------------------------


class TestDevFallbacks:
    def test_debug_true_constructs_with_no_guards_enforced(self, monkeypatch):
        _clear_guarded_env(monkeypatch)
        monkeypatch.setenv("DEBUG", "true")
        # Deliberately loose/unset: no SECRET_KEY, no APP_DATABASE_URL,
        # ALLOW_UNAUTHENTICATED_QUERY true, wildcard CORS.
        monkeypatch.setenv("ALLOW_UNAUTHENTICATED_QUERY", "true")
        monkeypatch.setenv("CORS_ALLOW_ORIGINS", "*")

        s = Settings(_env_file=None)  # must not raise

        assert s.DEBUG is True
        assert s.APP_DATABASE_URL == "sqlite:///./data/app.db"

    def test_app_database_url_does_not_fall_back_to_database_url(self, monkeypatch):
        _clear_guarded_env(monkeypatch)
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.setenv("DATABASE_URL", "postgresql://distinctive-erp-host/erp")
        monkeypatch.delenv("APP_DATABASE_URL", raising=False)

        s = Settings(_env_file=None)

        assert s.DATABASE_URL == "postgresql://distinctive-erp-host/erp"
        assert s.APP_DATABASE_URL == "sqlite:///./data/app.db"
        assert s.APP_DATABASE_URL != s.DATABASE_URL
