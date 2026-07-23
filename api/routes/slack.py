import json
import re

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from adapters.slack import already_processed, process_event, verify_signature
from core.config import settings
from core.executor import agent_executor

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.post("/webhook/slack")
async def slack_webhook(request: Request):
    """
    Receive Slack Events API payloads.

    Protocol:
    1. Verify X-Slack-Signature for ALL requests — reject unsigned with 403.
    2. Handle url_verification challenge (Slack signs these too).
    3. Ack with 200 immediately — Slack requires a response within 3 seconds.
    4. Dispatch the actual query to core.executor.agent_executor — the same
       bounded pool /query's SSE path uses, decoupled from Starlette's own
       default threadpool that regular request handling shares.

    Supported event types: app_mention, message.im
    """
    raw_body = await request.body()

    # Step 1: Parse payload (needed for url_verification challenge extraction).
    try:
        payload = json.loads(raw_body)
    except Exception:
        payload = {}

    # Step 2: Verify signature for ALL requests — including url_verification.
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    if not verify_signature(
        signing_secret=settings.SLACK_SIGNING_SECRET.get_secret_value(),
        request_timestamp=timestamp,
        request_body=raw_body,
        slack_signature=signature,
    ):
        logger.warning("slack_signature_verification_failed", timestamp=timestamp)
        raise HTTPException(status_code=403, detail="Invalid Slack signature.")

    # Step 3: Handle url_verification challenge after signature check.
    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge", "")})

    # Step 4: Dispatch event.
    if payload.get("type") == "event_callback":
        # Drop Slack retries / replays of an event we've already handled, so we
        # don't re-run the agent and double-post. Ack 200 either way.
        if already_processed(payload.get("event_id")):
            logger.info("slack_duplicate_event_ignored", event_id=payload.get("event_id"))
            return JSONResponse({"ok": True})

        event = payload.get("event", {})
        etype = event.get("type")

        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return JSONResponse({"ok": True})

        is_supported_event = etype == "app_mention" or (
            etype == "message" and event.get("channel_type") == "im"
        )

        if is_supported_event:
            slack_user_id = event.get("user")
            text = event.get("text", "").strip()
            channel = event.get("channel", "")
            thread_ts = event.get("thread_ts") or event.get("ts", "")

            if etype == "app_mention":
                text = re.sub(r"^<@[A-Z0-9]+>\s*", "", text).strip()

            if slack_user_id and text:
                agent_executor.submit(
                    process_event,
                    slack_user_id=slack_user_id,
                    text=text,
                    channel=channel,
                    thread_ts=thread_ts,
                    message_ts=event.get("ts", ""),
                    is_dm=event.get("channel_type") == "im",
                )

    return JSONResponse({"ok": True})
