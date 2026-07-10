"""
Tests for Slack event de-duplication.

These tests cover:
  - already_processed() unit behavior: first-sight vs. repeat, independent
    ids, None/empty ids never counting as duplicates, and TTL eviction
  - the /webhook/slack route: a redelivered event_callback (same event_id)
    must not be dispatched to process_event a second time

No Slack API calls or database are involved.
"""

import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import adapters.slack as slack_adapter
from adapters.slack import already_processed
from core.config import settings

# ---------------------------------------------------------------------------
# Isolation — _seen_events is module-global state shared across tests.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_seen_events():
    slack_adapter._seen_events.clear()
    yield
    slack_adapter._seen_events.clear()


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
        fake_now = [0.0]

        def fake_monotonic():
            return fake_now[0]

        monkeypatch.setattr(slack_adapter.time, "monotonic", fake_monotonic)

        assert already_processed("Ev_TTL") is False
        assert already_processed("Ev_TTL") is True

        # Advance the fake clock past the TTL — the entry must be evicted.
        fake_now[0] = slack_adapter._SEEN_EVENT_TTL_SECONDS + 1

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
    """Minimal FastAPI app mounting only the Slack router — no DB/lifespan."""
    monkeypatch.setattr(settings, "SLACK_SIGNING_SECRET", _SIGNING_SECRET)
    app = FastAPI()
    from api.routes.slack import router as slack_router

    app.include_router(slack_router)
    return TestClient(app)


class TestWebhookDedupe:
    def test_redelivered_event_is_not_dispatched_twice(self, slack_app, monkeypatch):
        mock_process_event = MagicMock()
        monkeypatch.setattr("api.routes.slack.process_event", mock_process_event)

        body = _event_callback_payload("Ev_DEDUPE_TEST")

        first = slack_app.post("/webhook/slack", content=body, headers=_sign(body))
        assert first.status_code == 200
        assert mock_process_event.call_count == 1

        # Slack redelivers the identical event (same event_id), freshly signed.
        second = slack_app.post("/webhook/slack", content=body, headers=_sign(body))
        assert second.status_code == 200
        assert mock_process_event.call_count == 1
