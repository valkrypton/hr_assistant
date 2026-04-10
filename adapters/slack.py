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
    # Slack block text has a 3000-char limit per section block.
    MAX = 2900
    body = answer if len(answer) <= MAX else answer[:MAX] + "…"

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
        ))
        session.commit()


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
    if hr_user:
        rbac_ctx = RBACContext.for_user(hr_user)
        employee_id = hr_user.employee_id
        role = hr_user.role
    else:
        # Unregistered user — no RBAC context; agent runs without scope.
        rbac_ctx = None
        employee_id = None
        role = None
        logger.warning("Slack user %s is not registered in hr_assistant_users.", slack_user_id)

    try:
        answer, tables_accessed = agent_query(text, rbac_ctx=rbac_ctx)

        _write_audit(
            slack_user_id=slack_user_id,
            employee_id=employee_id,
            role=role,
            question=text,
            answer=answer,
            tables_accessed=tables_accessed or None,
        )

        client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            blocks=_format_blocks(answer),
            text=answer,  # fallback plain text for notifications
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
