import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse

from adapters.slack import process_event, verify_signature
from core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/webhook/slack")
async def slack_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive Slack Events API payloads.

    Protocol:
    1. Verify X-Slack-Signature for ALL requests — reject unsigned with 403.
    2. Handle url_verification challenge (Slack signs these too).
    3. Ack with 200 immediately — Slack requires a response within 3 seconds.
    4. Dispatch the actual query to a background task.

    Supported event types: app_mention, message.im
    """
    import re

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
        signing_secret=settings.SLACK_SIGNING_SECRET,
        request_timestamp=timestamp,
        request_body=raw_body,
        slack_signature=signature,
    ):
        logger.warning("Slack signature verification failed — ts=%s", timestamp)
        raise HTTPException(status_code=403, detail="Invalid Slack signature.")

    # Step 3: Handle url_verification challenge after signature check.
    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge", "")})

    # Step 4: Dispatch event.
    if payload.get("type") == "event_callback":
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
                background_tasks.add_task(
                    process_event,
                    slack_user_id=slack_user_id,
                    text=text,
                    channel=channel,
                    thread_ts=thread_ts,
                )

    return JSONResponse({"ok": True})
