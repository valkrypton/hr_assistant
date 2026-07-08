"""
Slack adapter — receives Events API payloads, enforces RBAC, and posts Block Kit replies.

Responsibilities
----------------
- Verify X-Slack-Signature on every inbound request (HMAC-SHA256).
- Handle the URL-verification challenge sent during app setup.
- Parse app_mention and message.im events to extract user + text.
- Look up the HRUser for the Slack user ID and build an AgentContext.
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
import time

import structlog
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from core.agent import query as agent_query
from core.config import settings
from core.db import db_session
from core.identity.context import AgentContext
from core.identity.resolver import lookup_hr_user
from core.rate_limit import count_recent_queries
from core.telemetry.audit import write_audit

logger = structlog.get_logger(__name__)

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
    import ssl

    import certifi

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    client = WebClient(token=settings.SLACK_BOT_TOKEN, ssl=ssl_ctx)

    # Resolve identity and rate-limit count in one short-lived session,
    # closed before any Slack API call or the agent_query() call below —
    # neither should hold a pool connection idle for their duration (the
    # agent call alone can take up to ~15s).
    t_lookup = time.monotonic()
    with db_session() as session:
        hr_user = lookup_hr_user(session, slack_user_id)
        user_lookup_ms = int((time.monotonic() - t_lookup) * 1000)

        rate_check_ms = 0
        rate_count = None
        if hr_user and hr_user.role and settings.RATE_LIMIT_PER_HOUR > 0:
            t_rate = time.monotonic()
            rate_count = count_recent_queries(session, slack_user_id)
            rate_check_ms = int((time.monotonic() - t_rate) * 1000)

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

    if not hr_user.role:
        logger.warning("slack_user_missing_role", slack_user_id=slack_user_id)
        try:
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="Your account has no role assigned. Please ask your HR admin to set your role.",
            )
        except Exception:
            pass
        return

    ctx = AgentContext.for_user(hr_user, slack_user_id=slack_user_id)
    rbac_ctx = ctx.rbac
    employee_id = ctx.employee_id
    role = ctx.role.value

    # Rate limit check (count was already fetched above) — post a friendly
    # message and bail if exceeded.
    limit = settings.RATE_LIMIT_PER_HOUR
    if limit > 0 and rate_count is not None and rate_count >= limit:
        try:
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=f"You've reached the limit of {limit} queries per hour. Please try again later.",
            )
        except Exception:
            pass
        return

    # Fetch bot's own user ID once so we can identify its messages in the thread.
    t_history = time.monotonic()
    try:
        bot_user_id = client.auth_test()["user_id"]
    except Exception:
        bot_user_id = None

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
            rate_check_ms=rate_check_ms,
            history_fetch_ms=history_fetch_ms,
            agent_ms=result.agent_ms,
            slack_post_ms=slack_post_ms,
            total_agent_ms=result.total_ms,
        )

        with db_session() as session:
            write_audit(
                session,
                slack_user_id=slack_user_id,
                employee_id=employee_id,
                role=role,
                question=text,
                answer=result.answer,
                tables_accessed=result.tables_accessed or None,
                schema_rag_ms=result.schema_rag_ms,
                agent_ms=result.agent_ms,
                total_ms=result.total_ms,
                prompt_tokens=result.prompt_tokens or None,
                completion_tokens=result.completion_tokens or None,
                total_tokens=result.total_tokens or None,
                user_lookup_ms=user_lookup_ms,
                rate_check_ms=rate_check_ms,
                history_fetch_ms=history_fetch_ms,
                slack_post_ms=slack_post_ms,
            )

    except SlackApiError as exc:
        logger.error("slack_api_error_posting_reply", error=exc.response["error"])
    except Exception as exc:
        logger.exception("slack_event_processing_failed", slack_user_id=slack_user_id)
        with db_session() as session:
            write_audit(
                session,
                slack_user_id=slack_user_id,
                employee_id=employee_id,
                role=role,
                question=text,
                error=str(exc),
            )
        # Best-effort error reply — don't let this raise.
        try:
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="Sorry, I ran into an error processing your request. Please try again.",
            )
        except Exception:
            pass
