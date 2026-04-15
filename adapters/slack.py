"""
Slack adapter — receives Events API payloads, enforces RBAC, and posts Block Kit replies.

Responsibilities
----------------
- Verify X-Slack-Signature on every inbound request (HMAC-SHA256).
- Handle the URL-verification challenge sent during app setup.
- Parse app_mention and message.im events to extract user + text.
- Look up the HRUser for the Slack user ID and build an RBACContext.
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
import logging
import time
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from sqlalchemy.orm import Session

from core.agent import query as agent_query
from core.config import settings
from core.rbac.context import RBACContext
from core.rbac.models import AuditLog, HRUser

logger = logging.getLogger(__name__)

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

    base = f"v0:{request_timestamp}:{request_body.decode('utf-8')}"
    expected = "v0=" + hmac.new(
        signing_secret.encode("utf-8"),
        base.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

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
# DB helpers  (import-time engine avoidance — use the app engine lazily)
# ---------------------------------------------------------------------------

def _get_app_engine():
    import sqlalchemy
    return sqlalchemy.create_engine(settings.APP_DATABASE_URL)


def _lookup_user(slack_user_id: str) -> Optional[HRUser]:
    with Session(_get_app_engine()) as session:
        return (
            session.query(HRUser)
            .filter_by(slack_user_id=slack_user_id, is_active=True)
            .first()
        )


def _write_audit(
    *,
    slack_user_id: str,
    employee_id: Optional[int],
    role: Optional[str],
    question: str,
    answer: Optional[str] = None,
    tables_accessed: Optional[str] = None,
    error: Optional[str] = None,
    schema_rag_ms: Optional[int] = None,
    agent_ms: Optional[int] = None,
    total_ms: Optional[int] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
) -> None:
    with Session(_get_app_engine()) as session:
        session.add(AuditLog(
            slack_user_id=slack_user_id,
            employee_id=employee_id,
            role=role,
            question=question,
            answer=answer,
            tables_accessed=tables_accessed,
            error=error,
            schema_rag_ms=schema_rag_ms,
            agent_ms=agent_ms,
            total_ms=total_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        ))
        session.commit()


# ---------------------------------------------------------------------------
# Thread history  (FR: conversation continuity within a Slack thread)
# ---------------------------------------------------------------------------

_HISTORY_MAX_TURNS = 10  # max prior turns to include (5 exchanges)


def _fetch_thread_history(
    client: WebClient,
    channel: str,
    thread_ts: str,
    bot_user_id: Optional[str],
    current_text: str,
) -> list[dict]:
    """
    Fetch prior messages in a Slack thread and return them as a list of
    {"role": "user"|"assistant", "content": "..."} dicts, oldest first,
    excluding the current (just-arrived) message.

    Returns an empty list on any error so a history failure never blocks
    the main query.
    """
    try:
        resp = client.conversations_replies(
            channel=channel,
            ts=thread_ts,
            limit=_HISTORY_MAX_TURNS + 5,  # fetch a few extra to account for skipped msgs
        )
        messages = resp.get("messages", [])
    except Exception as exc:
        logger.warning("Failed to fetch thread history: %s", exc)
        return []

    history: list[dict] = []
    for msg in messages:
        text = msg.get("text", "").strip()
        if not text:
            continue
        is_bot = (bot_user_id and msg.get("user") == bot_user_id) or msg.get("bot_id")
        # Skip the current message (it arrives as the last message in the thread).
        if not is_bot and text == current_text:
            continue
        role = "assistant" if is_bot else "user"
        # Strip Slack mrkdwn bot-mention prefix (e.g. "<@U123> ") from user messages.
        if role == "user" and text.startswith("<@"):
            text = text.split(">", 1)[-1].strip()
        history.append({"role": role, "content": text})

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
) -> None:
    """
    Look up the user, run the agent with RBAC scope, and post the answer
    as a Block Kit card inside the original thread.

    This function is intentionally synchronous so it can be called from a
    FastAPI BackgroundTask without requiring an event loop.
    """
    import ssl, certifi
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    client = WebClient(token=settings.SLACK_BOT_TOKEN, ssl=ssl_ctx)

    # Resolve identity and build RBAC context.
    hr_user = _lookup_user(slack_user_id)
    if not hr_user:
        logger.warning("Slack user %s is not registered in hr_assistant_users.", slack_user_id)
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
        logger.warning("Slack user %s has no role assigned.", slack_user_id)
        try:
            client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text="Your account has no role assigned. Please ask your HR admin to set your role.",
            )
        except Exception:
            pass
        return

    rbac_ctx = RBACContext.for_user(hr_user)
    employee_id = hr_user.employee_id
    role = hr_user.role

    # Rate limit check — post a friendly message and bail if exceeded.
    if hr_user:
        from datetime import datetime, timedelta, timezone
        from sqlalchemy.orm import Session
        limit = settings.RATE_LIMIT_PER_HOUR
        if limit > 0:
            since = datetime.now(timezone.utc) - timedelta(hours=1)
            with Session(_get_app_engine()) as session:
                count = (
                    session.query(AuditLog)
                    .filter(
                        AuditLog.slack_user_id == slack_user_id,
                        AuditLog.created_at >= since,
                    )
                    .count()
                )
            if count >= limit:
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
        current_text=text,
    )

    try:
        result = agent_query(text, rbac_ctx=rbac_ctx, conversation_history=conversation_history or None)

        _write_audit(
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
        )

        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            blocks=_format_blocks(result.answer),
            text=result.answer,  # fallback plain text for notifications
        )

    except SlackApiError as exc:
        logger.error("Slack API error posting reply: %s", exc.response["error"])
    except Exception as exc:
        logger.exception("Error processing Slack event for user %s", slack_user_id)
        _write_audit(
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
