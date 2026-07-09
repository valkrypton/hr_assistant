"""
Tests for Slack event de-duplication (PR10 guardrail).

Slack redelivers an event (same event_id) if we don't ack fast enough, or an
identical signed body replays within the 5-minute signature window. Without
dedupe, each redelivery re-runs the agent and re-posts the answer.

Covers:
  - adapters.slack.already_processed() — in-process TTL set semantics
  - /webhook/slack route — a duplicate event_id is acked but not re-dispatched
    to process_event
"""

import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import adapters.slack as slack_module
import api.routes.slack as slack_routes
from adapters.slack import already_processed
from core.config import settings


@pytest.fixture(autouse=True)
def _clear_seen_events():
    """The _seen_events dict is module-global — reset it around every test so
    dedupe state can't leak between tests (or into the rest of the suite)."""
    slack_module._seen_events.clear()
    yield
    slack_module._seen_events.clear()


# ---------------------------------------------------------------------------
# already_processed — unit tests
# ---------------------------------------------------------------------------


class TestAlreadyProcessed:
    def test_first_sight_returns_false_immediate_repeat_returns_true(self):
        assert already_processed("evt-1") is False
        assert already_processed("evt-1") is True

    def test_different_id_is_independent(self):
        assert already_processed("evt-a") is False
        assert already_processed("evt-b") is False
        # evt-a was already recorded by the first call above.
        assert already_processed("evt-a") is True

    def test_none_id_is_never_a_duplicate(self):
        assert already_processed(None) is False
        assert already_processed(None) is False

    def test_empty_id_is_never_a_duplicate(self):
        assert already_processed("") is False
        assert already_processed("") is False

    def test_ttl_eviction_allows_reprocessing_after_expiry(self, monkeypatch):
        fake_now = [1_000.0]
        monkeypatch.setattr(slack_module.time, "monotonic", lambda: fake_now[0])

        assert already_processed("evt-ttl") is False
        assert already_processed("evt-ttl") is True  # still within TTL

        # Advance the fake clock past the TTL so the recorded entry expires.
        fake_now[0] += slack_module._SEEN_EVENT_TTL_SECONDS + 1

        # The expired entry is evicted on this call, so the id is "new" again.
        assert already_processed("evt-ttl") is False


# ---------------------------------------------------------------------------
# /webhook/slack — route-level dedupe
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: str, ts: str) -> str:
    base = f"v0:{ts}:{body.decode('utf-8')}"
    return (
        "v0=" + hmac.new(secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).hexdigest()
    )


@pytest.fixture()
def slack_client(monkeypatch):
    """Minimal FastAPI app carrying only the Slack router — no DB/lifespan
    needed since /webhook/slack doesn't touch either once process_event is
    patched out."""
    monkeypatch.setattr(settings, "SLACK_SIGNING_SECRET", "test-signing-secret")
    app = FastAPI()
    app.include_router(slack_routes.router)
    with TestClient(app) as c:
        yield c


def _event_callback_body(event_id: str) -> bytes:
    payload = {
        "type": "event_callback",
        "event_id": event_id,
        "event": {
            "type": "app_mention",
            "user": "U123",
            "text": "<@BOT123> how many people report to me?",
            "channel": "C123",
            "ts": "1700000000.000100",
        },
    }
    return json.dumps(payload).encode()


def _post_signed(client: TestClient, event_id: str):
    body = _event_callback_body(event_id)
    ts = str(int(time.time()))
    sig = _sign(body, settings.SLACK_SIGNING_SECRET, ts)
    return client.post(
        "/webhook/slack",
        content=body,
        headers={
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": sig,
            "Content-Type": "application/json",
        },
    )


class TestWebhookDedupe:
    def test_duplicate_event_id_is_not_dispatched_twice(self, slack_client, monkeypatch):
        mock_process = MagicMock()
        monkeypatch.setattr(slack_routes, "process_event", mock_process)

        r1 = _post_signed(slack_client, event_id="Ev_DEDUPE_1")
        assert r1.status_code == 200
        assert r1.json() == {"ok": True}
        assert mock_process.call_count == 1

        # Same event_id redelivered (e.g. a Slack retry) — must ack 200 but
        # NOT schedule process_event a second time.
        r2 = _post_signed(slack_client, event_id="Ev_DEDUPE_1")
        assert r2.status_code == 200
        assert r2.json() == {"ok": True}
        assert mock_process.call_count == 1
