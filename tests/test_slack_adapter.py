"""
Unit tests for adapters/slack.py.

These tests cover:
  - verify_signature() guarding against malformed (non-UTF8) request bodies
  - _fetch_thread_history() scoping thread context to the requester's own
    turns, so a shared channel thread cannot leak another user's Q&A
    (and RBAC scope) into the agent's conversation history

No Slack API calls or database are involved — the WebClient is a stub.
"""
import time

from adapters.slack import _fetch_thread_history, verify_signature


# ---------------------------------------------------------------------------
# verify_signature — malformed body must not raise
# ---------------------------------------------------------------------------

class TestVerifySignature:
    def test_malformed_utf8_body_returns_false(self):
        ts = str(int(time.time()))
        # 0xFF is never valid UTF-8 — decode("utf-8") would raise UnicodeDecodeError.
        malformed_body = b"\xff\xfe not valid utf-8"
        result = verify_signature("some-signing-secret", ts, malformed_body, "v0=irrelevant")
        assert result is False

    def test_valid_body_still_verifies(self):
        # Sanity check — the guard must not break the happy path.
        import hashlib
        import hmac

        secret = "some-signing-secret"
        ts = str(int(time.time()))
        body = b'{"type":"event_callback"}'
        base = f"v0:{ts}:{body.decode('utf-8')}"
        expected = "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()
        assert verify_signature(secret, ts, body, expected) is True


# ---------------------------------------------------------------------------
# _fetch_thread_history — per-requester scoping
# ---------------------------------------------------------------------------

class StubClient:
    """Minimal stand-in for slack_sdk.WebClient — only conversations_replies is used."""

    def __init__(self, messages):
        self._messages = messages

    def conversations_replies(self, **kwargs):
        return {"messages": self._messages}


class TestFetchThreadHistory:
    BOT_ID = "BOT1"
    REQUESTER = "U_REQUESTER"
    OTHER = "U_OTHER"

    def fetch(self, messages, current_text):
        client = StubClient(messages)
        return _fetch_thread_history(
            client=client,
            channel="C123",
            thread_ts="111.111",
            bot_user_id=self.BOT_ID,
            current_text=current_text,
            requester_user_id=self.REQUESTER,
        )

    def test_excludes_other_users_question_and_bot_reply_to_them(self):
        messages = [
            {"user": self.OTHER, "text": "what is the other user's salary band?"},
            {"user": self.BOT_ID, "bot_id": "B1", "text": "reply meant for the other user"},
            {"user": self.REQUESTER, "text": "current question"},
        ]
        history = self.fetch(messages, current_text="current question")
        contents = [h["content"] for h in history]
        assert "what is the other user's salary band?" not in contents
        assert "reply meant for the other user" not in contents

    def test_includes_requesters_own_prior_turn_and_bot_reply(self):
        messages = [
            {"user": self.REQUESTER, "text": "earlier question"},
            {"user": self.BOT_ID, "bot_id": "B1", "text": "earlier answer"},
            {"user": self.REQUESTER, "text": "current question"},
        ]
        history = self.fetch(messages, current_text="current question")
        assert history == [
            {"role": "user", "content": "earlier question"},
            {"role": "assistant", "content": "earlier answer"},
        ]

    def test_excludes_current_message(self):
        messages = [
            {"user": self.REQUESTER, "text": "current question"},
        ]
        history = self.fetch(messages, current_text="current question")
        assert history == []
