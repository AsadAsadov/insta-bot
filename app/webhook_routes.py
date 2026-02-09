from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app import db
from app.meta_client import send_ig_dm

logger = logging.getLogger("insta-bot")

router = APIRouter()

VERIFY_TOKEN_ENV = "META_VERIFY_TOKEN"
APP_SECRET_ENV = "META_APP_SECRET"


def _verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    app_secret = os.getenv(APP_SECRET_ENV)
    if not app_secret:
        logger.info("signature_valid reason=app_secret_not_configured")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        logger.warning("signature_invalid reason=missing_or_malformed_header")
        return False
    provided = signature_header.split("sha256=", 1)[1]
    expected = hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(provided, expected):
        logger.warning("signature_invalid reason=hash_mismatch")
        return False
    logger.info("signature_valid")
    return True


@router.get("/webhook", response_class=PlainTextResponse, response_model=None)
def verify_webhook(request: Request) -> PlainTextResponse:
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")
    verify_token = os.getenv(VERIFY_TOKEN_ENV)
    if (
        hub_mode == "subscribe"
        and verify_token
        and hub_verify_token == verify_token
        and hub_challenge
    ):
        return PlainTextResponse(content=hub_challenge, status_code=200)
    return PlainTextResponse(content="Forbidden", status_code=403)


@router.head("/webhook")
def webhook_head() -> PlainTextResponse:
    return PlainTextResponse(content="", status_code=200)


@router.post("/webhook")
async def receive_webhook(request: Request) -> JSONResponse:
    raw_body = await request.body()
    signature_header = request.headers.get("x-hub-signature-256")
    if not _verify_signature(raw_body, signature_header):
        return JSONResponse(content={"ok": True, "ignored": "invalid_signature"})
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        logger.exception("webhook_received parse_error=true")
        return JSONResponse(content={"ok": True, "ignored": "invalid_json"})
    logger.info("webhook_received payload_keys=%s", list(payload.keys()))
    if isinstance(payload, dict) and payload.get("object") == "instagram":
        _process_instagram_payload(payload)
    return JSONResponse(content={"ok": True})


def _process_instagram_payload(payload: dict[str, Any]) -> None:
    entries = payload.get("entry") or []
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        messaging_events = entry.get("messaging") or []
        if not isinstance(messaging_events, list):
            continue
        for messaging_event in messaging_events:
            if not isinstance(messaging_event, dict):
                continue
            _handle_messaging_event(messaging_event)


def _handle_messaging_event(event: dict[str, Any]) -> None:
    sender_id = (event.get("sender") or {}).get("id")
    recipient_id = (event.get("recipient") or {}).get("id")
    message = event.get("message") or {}
    if not sender_id:
        return
    message_text = message.get("text") if isinstance(message, dict) else None
    message_id = message.get("mid") if isinstance(message, dict) else None
    timestamp = event.get("timestamp") or int(time.time())
    try:
        db.upsert_thread(sender_id, message_text or "", int(timestamp))
        db.insert_event(
            thread_id=sender_id,
            event_type="message_in",
            message_id=message_id,
            text=message_text,
            from_id=sender_id,
            ts=int(timestamp),
        )
        logger.info("db_write_success action=message_in thread_id=%s", sender_id)
    except Exception:  # noqa: BLE001
        logger.exception("db_write_fail action=message_in thread_id=%s", sender_id)
        return

    template = db.find_matching_template(message_text)
    if template:
        reply_text = template["reply_text"]
        _send_auto_reply(sender_id, reply_text)

    if recipient_id:
        logger.info(
            "webhook_received message_in sender=%s recipient=%s", sender_id, recipient_id
        )


def _send_auto_reply(thread_id: str, reply_text: str) -> None:
    outbox_id = db.create_outbox(thread_id, reply_text)
    result = send_ig_dm(thread_id, reply_text)
    status = "sent" if result.get("ok") else "failed"
    error_text = None
    if not result.get("ok"):
        error_text = str(result.get("error") or result.get("response"))
    db.update_outbox(outbox_id, status, error_text, db.utc_now_iso())
    db.insert_event(
        thread_id=thread_id,
        event_type="message_out",
        message_id=(result.get("response") or {}).get("message_id")
        if isinstance(result.get("response"), dict)
        else None,
        text=reply_text,
        from_id="page",
        ts=int(time.time()),
    )


__all__ = ["router"]
