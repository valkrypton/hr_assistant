"""
Tests for Slack event de-duplication.

These tests cover:
  - already_processed() unit behavior: first-sight vs. repeat, independent
    ids, None/empty ids never counting as duplicates, and TTL eviction
  - the /webhook/slack route: a redelivered event_callback (same event_id)
    must not be dispatched to process_event a second time

Dedupe state lives in the slack_seen_events table (core/rbac/models.py) now,
not an in-process dict — see adapters/slack.py.already_processed. Each test
gets its own throwaway sqlite APP_DATABASE_URL via the autouse _test_app_db
fixture below, so tests are isolated by a fresh DB rather than by clearing
shared in-process state.
"""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import adapters.slack as slack_adapter
from adapters.slack import already_processed
from core.config import settings

# ---------------------------------------------------------------------------
# Isolation — slack_seen_events now lives in APP_DATABASE_URL; point it at a
# fresh throwaway sqlite file per test and create the schema.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _test_app_db(monkeypatch, tmp_path):
    from core.db import app_engine
    from core.rbac.models import Base

    db_path = tmp_path / "dedupe_test.db"
    monkeypatch.setattr(settings, "APP_DATABASE_URL", f"sqlite:///{db_path}")
    app_engine.cache_clear()
    Base.metadata.create_all(app_engine())
    yield
    app_engine.cache_clear()


# ---------------------------------------------------------------------------
# already_processed — unit tests
# ---------------------------------------------------------------------------


class TestAlreadyProcessed:
    def test_first_sight_returns_false_then_repeat_returns_true(self):
        assert already_processed("Ev001") is False
        assert already_processed("Ev001") is True

    def test_different_event_id_is_independent(self):
        assert already_processed("Ev001") is False
        assert already_processed("Ev002") is False
        # Ev001 is still a duplicate; Ev002 was only seen once so far.
        assert already_processed("Ev001") is True

    def test_none_event_id_never_a_duplicate(self):
        assert already_processed(None) is False
        assert already_processed(None) is False

    def test_empty_event_id_never_a_duplicate(self):
        assert already_processed("") is False
        assert already_processed("") is False

    def test_ttl_eviction_allows_reprocessing_after_expiry(self, monkeypatch):
        base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

        class _FrozenDateTime(datetime):
            _now = base_time

            @classmethod
            def now(cls, tz=None):
                return cls._now

        monkeypatch.setattr(slack_adapter, "datetime", _FrozenDateTime)

        assert already_processed("Ev_TTL") is False
        assert already_processed("Ev_TTL") is True

        # Advance the fake clock past the TTL — the row must be cleaned up
        # and reprocessing allowed.
        _FrozenDateTime._now = base_time + timedelta(
            seconds=slack_adapter._SEEN_EVENT_TTL_SECONDS + 1
        )

        assert already_processed("Ev_TTL") is False


# ---------------------------------------------------------------------------
# /webhook/slack — a redelivered event must not be dispatched twice
# ---------------------------------------------------------------------------

_SIGNING_SECRET = "test-slack-signing-secret"


def _sign(body: bytes, secret: str = _SIGNING_SECRET) -> dict[str, str]:
    ts = str(int(time.time()))
    base = f"v0:{ts}:{body.decode('utf-8')}"
    signature = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": signature,
        "Content-Type": "application/json",
    }


def _event_callback_payload(event_id: str) -> bytes:
    payload = {
        "type": "event_callback",
        "event_id": event_id,
        "event": {
            "type": "app_mention",
            "user": "U_REQUESTER",
            "text": "<@BOT1> what is my leave balance?",
            "channel": "C123",
            "ts": "1700000001.000100",
        },
    }
    return json.dumps(payload).encode("utf-8")


@pytest.fixture
def slack_app(monkeypatch):
    """Minimal FastAPI app mounting only the Slack router — no lifespan.
    already_processed() still hits the real (throwaway) app DB set up by
    the autouse _test_app_db fixture above."""
    monkeypatch.setattr(settings, "SLACK_SIGNING_SECRET", _SIGNING_SECRET)
    app = FastAPI()
    from api.routes.slack import router as slack_router

    app.include_router(slack_router)
    return TestClient(app)
