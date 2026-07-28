"""
DEPRECATED — superseded by Google SSO login (api/routes/auth.py). Kept as-is
for now; may be removed later. New end-user access should go through the
session-cookie path in api/routes/query.py, not Slack registration.

Slack adapter — receives Events API payloads, enforces RBAC, and posts Block Kit replies.

Responsibilities
----------------
- Verify X-Slack-Signature on every inbound request (HMAC-SHA256).
- Handle the URL-verification challenge sent during app setup.
- Parse app_mention and message.im events to extract user + text.
- Look up the HRUser for the Slack user ID, resolve its ERP access level, and
  build an RBACContext.
- Call core.agent.query() and post the answer as a Block Kit card in-thread.

Slack's 3-second rule
---------------------
Slack expects an HTTP 200 within 3 seconds of delivering an event.  The agent
can take up to 15 seconds.  The FastAPI route acks immediately and offloads the
actual work to a BackgroundTask so the connection closes before the agent runs.
"""

from __future__ import annotations

import hashlib
import hmac
import ssl
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import certifi
import structlog
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from core.agent import query as agent_query
from core.config import settings
from core.db import db_session
from core.rbac.models import SlackSeenEvent
from core.rbac.repository import HRUserRepository
from core.rbac.resolution import resolve_context

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Event de-duplication
# ---------------------------------------------------------------------------
# Slack redelivers an event (same event_id, X-Slack-Retry-Num header) if we
# don't ack within 3s — and an identical signed body replays within the 5-minute
# signature window. Without dedupe each redelivery re-runs the agent and
# re-posts. Backed by the slack_seen_events table (core/rbac/models.py) rather
# than an in-process dict, so dedupe works across worker processes, not just
# within one. The event_id primary key gives atomic "first sight wins"
# semantics under concurrent inserts — a duplicate insert raises
# IntegrityError, which is exactly the signal a redelivery should produce.
_SEEN_EVENT_TTL_SECONDS = 600


def already_processed(event_id: str | None) -> bool:
    """True if this Slack event_id was seen within the TTL. First sight
    records it and returns False. Empty/missing id is never treated as a
    duplicate."""
    if not event_id:
        return False
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=_SEEN_EVENT_TTL_SECONDS)
    with db_session() as session:
        # Opportunistic cleanup so the table can't grow without bound.
        session.query(SlackSeenEvent).filter(SlackSeenEvent.seen_at < cutoff).delete()
        try:
            session.add(SlackSeenEvent(event_id=event_id, seen_at=now))
            session.commit()
        except IntegrityError:
            session.rollback()
            return True
        return False


# ---------------------------------------------------------------------------
# Signature verification  (FR-7.1, security)
# ---------------------------------------------------------------------------


def verify_signature(
    signing_secret: str,
    request_timestamp: str,
    request_body: bytes,
    slack_signature: str,
) -> bool:
    """
    Return True if the X-Slack-Signature header matches the expected HMAC.
    Rejects requests older than 5 minutes to prevent replay attacks.
    """
    try:
        ts = int(request_timestamp)
    except (TypeError, ValueError):
        return False

    if abs(time.time() - ts) > 300:  # 5-minute replay window
        return False

    try:
        decoded_body = request_body.decode("utf-8")
    except UnicodeDecodeError:
        return False

    base = f"v0:{request_timestamp}:{decoded_body}"
    expected = (
        "v0="
        + hmac.new(
            signing_secret.encode("utf-8"),
            base.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
    )

    return hmac.compare_digest(expected, slack_signature)


# ---------------------------------------------------------------------------
# Block Kit formatter
# ---------------------------------------------------------------------------


def _format_blocks(answer: str) -> list[dict]:
    """
    Wrap an agent answer in a minimal Block Kit layout.

    Layout:
        [ Section — answer text ]
        [ Divider ]
        [ Context — "HR Assistant • powered by AI" ]
    """
    # Escape characters Slack's mrkdwn parser treats as special link syntax.
    safe = answer.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Slack block text has a 3000-char limit per section block.
    MAX = 2900
    body = safe if len(safe) <= MAX else safe[:MAX] + "…"

    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": body},
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "_HR Assistant_ · powered by AI · answers are scoped to your role",
                }
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Thread history  (FR: conversation continuity within a Slack thread)
# ---------------------------------------------------------------------------

_HISTORY_MAX_TURNS = 10  # max prior turns to include (5 exchanges)


@lru_cache(maxsize=1)
def _slack_client() -> WebClient:
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    return WebClient(token=settings.SLACK_BOT_TOKEN.get_secret_value(), ssl=ssl_ctx)


_bot_user_id_cache: str | None = None


def _bot_user_id() -> str | None:
    """The bot's own Slack user ID — fixed for the process lifetime, so
    fetched via auth_test() once instead of on every event. Not @lru_cache:
    a transient auth_test() failure on the first call must not pin None for
    the process lifetime (which would mislabel the bot's own thread messages
    as user turns until restart) — only a successful lookup is cached."""
    global _bot_user_id_cache
    if _bot_user_id_cache is not None:
        return _bot_user_id_cache
    try:
        _bot_user_id_cache = _slack_client().auth_test()["user_id"]
        return _bot_user_id_cache
    except Exception:
        return None


def _fetch_thread_history(
    client: WebClient,
    channel: str,
    thread_ts: str,
    bot_user_id: str | None,
    current_ts: str,
    requester_user_id: str,
    is_dm: bool,
) -> list[dict]:
    """
    Fetch prior messages in a Slack thread and return them as a list of
    {"role": "user"|"assistant", "content": "..."} dicts, oldest first,
    excluding the current (just-arrived) message — matched by its unique
    Slack ts, not its text, so a repeated question doesn't also drop the
    requester's earlier identical turns.

    Only the requester's own message turns are ever included. Bot replies are
    included only in a DM (is_dm=True), where the requester is the sole human
    so every bot answer is theirs. In a shared channel thread bot replies are
    dropped entirely: replies are posted asynchronously, so a bot answer to
    another user (with a different RBAC role) can land right after the
    requester's message and be misattributed to them, leaking a broader-scope
    answer into the requester's context.

    Returns an empty list on any error so a history failure never blocks
    the main query.
    """
    try:
        # Page from the current message (latest) so long threads use recent
        # context, not the oldest messages Slack returns by default.
        kwargs = dict(channel=channel, ts=thread_ts, limit=_HISTORY_MAX_TURNS + 5)
        if current_ts:
            kwargs.update(latest=current_ts, inclusive=True)
        resp = client.conversations_replies(**kwargs)
        messages = resp.get("messages", [])
    except Exception as exc:
        logger.warning("thread_history_fetch_failed", error=str(exc))
        return []

    history: list[dict] = []
    for msg in messages:
        # Skip the current (just-arrived) message by its unique ts.
        if msg.get("ts") == current_ts:
            continue
        text = msg.get("text", "").strip()
        if not text:
            continue
        is_bot = (bot_user_id and msg.get("user") == bot_user_id) or msg.get("bot_id")
        if is_bot:
            if is_dm:
                history.append({"role": "assistant", "content": text})
            continue  # channel threads: drop bot turns (cross-scope risk)
        if msg.get("user") != requester_user_id:
            continue
        # Strip Slack mrkdwn bot-mention prefix (e.g. "<@U123> ") from user messages.
        if text.startswith("<@"):
            text = text.split(">", 1)[-1].strip()
        history.append({"role": "user", "content": text})

    # Keep only the most recent N turns.
    return history[-_HISTORY_MAX_TURNS:]


# ---------------------------------------------------------------------------
# Core event processor  (runs in background — outside the 3-second window)
# ---------------------------------------------------------------------------


def process_event(
    slack_user_id: str,
    text: str,
    channel: str,
    thread_ts: str,
    message_ts: str = "",
    is_dm: bool = False,
) -> None:
    """
    Look up the user, run the agent with RBAC scope, and post the answer
    as a Block Kit card inside the original thread.

    message_ts is the ts of the just-arrived event message — used to exclude
    it from the thread history fetched for conversation continuity.
    is_dm marks a 1:1 direct message; in a shared channel thread bot replies
    are excluded from history (see _fetch_thread_history).

    This function is intentionally synchronous so it can be called from a
    FastAPI BackgroundTask without requiring an event loop.
    """
    client = _slack_client()

    # Resolve identity in one short-lived session,
    # closed before any Slack API call or the agent_query() call below —
    # neither should hold a pool connection idle for their duration (the
    # agent call alone can take up to ~15s).
    t_lookup = time.monotonic()
    with db_session() as session:
        hr_user = HRUserRepository.get_by_slack_user_id(session, slack_user_id)
        user_lookup_ms = int((time.monotonic() - t_lookup) * 1000)

    if not hr_user:
        logger.warning("slack_user_not_registered", slack_user_id=slack_user_id)
        try:
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="You're not registered to use HR Assistant. Please ask your HR admin to add your Slack account.",
            )
        except Exception:
            pass
        return

    # employee_id IS person.id — see core/rbac/models.py.
    try:
        rbac_ctx = resolve_context(hr_user.employee_id)
    except SQLAlchemyError as exc:
        logger.warning("slack_rbac_resolution_failed", slack_user_id=slack_user_id, error=str(exc))
        try:
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="I can't verify your access right now. Please try again in a minute.",
            )
        except Exception:
            pass
        return

    if rbac_ctx is None:
        logger.warning("slack_user_not_provisioned", slack_user_id=slack_user_id)
        try:
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="Your ERP account is inactive or not provisioned. Please contact HR.",
            )
        except Exception:
            pass
        return

    # Bot's own user ID — used to identify its messages in the thread.
    t_history = time.monotonic()
    bot_user_id = _bot_user_id()

    # Fetch prior thread turns for conversation continuity.
    conversation_history = _fetch_thread_history(
        client=client,
        channel=channel,
        thread_ts=thread_ts,
        bot_user_id=bot_user_id,
        current_ts=message_ts,
        requester_user_id=slack_user_id,
        is_dm=is_dm,
    )
    history_fetch_ms = int((time.monotonic() - t_history) * 1000)

    try:
        result = agent_query(
            text, rbac_ctx=rbac_ctx, conversation_history=conversation_history or None
        )

        t_post = time.monotonic()
        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            blocks=_format_blocks(result.answer),
            text=result.answer,  # fallback plain text for notifications
        )
        slack_post_ms = int((time.monotonic() - t_post) * 1000)

        logger.info(
            "process_event_timing",
            user_lookup_ms=user_lookup_ms,
            history_fetch_ms=history_fetch_ms,
            agent_ms=result.agent_ms,
            slack_post_ms=slack_post_ms,
            total_agent_ms=result.total_ms,
        )

    except SlackApiError as exc:
        logger.error("slack_api_error_posting_reply", error=exc.response["error"])
    except Exception:
        logger.exception("slack_event_processing_failed", slack_user_id=slack_user_id)
        # Best-effort error reply — don't let this raise.
        try:
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="Sorry, I ran into an error processing your request. Please try again.",
            )
        except Exception:
            pass
