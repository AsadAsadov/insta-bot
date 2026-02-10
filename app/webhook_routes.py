from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app import db
from app.meta_client import (
    send_ig_dm,
    send_private_comment_reply,
    send_public_comment_reply,
)

logger = logging.getLogger("insta-bot")

router = APIRouter()

VERIFY_TOKEN_ENV = "META_VERIFY_TOKEN"
APP_SECRET_ENV = "META_APP_SECRET"


def _verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    app_secret = os.getenv(APP_SECRET_ENV)
    if not app_secret:
        logger.info("signature_skipped reason=app_secret_not_configured")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        logger.warning("signature_invalid reason=missing_or_malformed_header")
        return False
    provided = signature_header.split("sha256=", 1)[1]
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided, expected):
        logger.warning("signature_invalid reason=hash_mismatch")
        return False
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
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        logger.exception("webhook_parse_failed")
        return JSONResponse(content={"ok": True, "ignored": "invalid_json"})

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
        if isinstance(messaging_events, list):
            for messaging_event in messaging_events:
                if isinstance(messaging_event, dict):
                    _handle_messaging_event(messaging_event)

        changes = entry.get("changes") or []
        if isinstance(changes, list):
            for change in changes:
                if isinstance(change, dict):
                    _handle_change_event(change)


def _handle_messaging_event(event: dict[str, Any]) -> None:
    sender_id = (event.get("sender") or {}).get("id")
    message = event.get("message") or {}
    if not sender_id or not isinstance(message, dict):
        return

    message_text = message.get("text")
    message_id = message.get("mid")
    timestamp = int(event.get("timestamp") or time.time())

    try:
        db.upsert_thread(sender_id, message_text or "", timestamp)
        db.insert_event(
            thread_id=sender_id,
            event_type="message_in",
            message_id=message_id,
            text=message_text,
            from_id=sender_id,
            ts=timestamp,
        )
    except Exception:  # noqa: BLE001
        logger.exception("message_in_store_failed thread_id=%s", sender_id)
        return

    template = db.find_matching_template(message_text)
    if template:
        _send_auto_reply(sender_id, template["reply_text"])


def _handle_change_event(change: dict[str, Any]) -> None:
    value = change.get("value")
    if not isinstance(value, dict):
        return

    comment_id = value.get("id") or value.get("comment_id")
    comment_text = value.get("text")
    commenter_id = value.get("from", {}).get("id") if isinstance(value.get("from"), dict) else None

    if not comment_id:
        return

    thread_id = commenter_id or f"comment:{comment_id}"
    ts = int(time.time())

    try:
        db.upsert_thread(thread_id, comment_text or "", ts)
        db.insert_event(
            thread_id=thread_id,
            event_type="comment_in",
            message_id=comment_id,
            text=comment_text,
            from_id=commenter_id,
            ts=ts,
        )
    except Exception:  # noqa: BLE001
        logger.exception("comment_in_store_failed comment_id=%s", comment_id)
        return

    trigger = db.find_matching_comment_trigger(comment_text)
    if not trigger:
        return

    _send_comment_public_reply(
        thread_id=thread_id,
        comment_id=comment_id,
        text=trigger["public_reply_text"],
    )
    _send_comment_private_reply(
        thread_id=thread_id,
        comment_id=comment_id,
        text=trigger["dm_reply_text"],
    )


def _send_auto_reply(thread_id: str, reply_text: str) -> None:
    outbox_id = db.create_outbox(thread_id, reply_text)
    result = send_ig_dm(thread_id, reply_text)
    status = "sent" if result.get("ok") else "failed"
    error_text = None if result.get("ok") else str(result.get("error") or result.get("json"))
    db.update_outbox(outbox_id, status, error_text, db.utc_now_iso())
    db.insert_event(
        thread_id=thread_id,
        event_type="message_out",
        message_id=(result.get("json") or {}).get("message_id") if isinstance(result.get("json"), dict) else None,
        text=reply_text,
        from_id="page",
        ts=int(time.time()),
    )


def _send_comment_public_reply(thread_id: str, comment_id: str, text: str) -> None:
    outbox_id = db.create_outbox(thread_id, text)
    try:
        result = send_public_comment_reply(comment_id, text)
    except Exception:  # noqa: BLE001
        logger.exception("comment_public_reply_failed comment_id=%s", comment_id)
        result = {"ok": False, "error": "unexpected_error", "json": None}

    status = "sent" if result.get("ok") else "failed"
    error_text = None if result.get("ok") else str(result.get("error") or result.get("json"))
    db.update_outbox(outbox_id, status, error_text, db.utc_now_iso())
    db.insert_event(
        thread_id=thread_id,
        event_type="comment_public_reply",
        message_id=comment_id,
        text=text,
        from_id="page",
        ts=int(time.time()),
    )


def _send_comment_private_reply(thread_id: str, comment_id: str, text: str) -> None:
    outbox_id = db.create_outbox(thread_id, text)
    try:
        result = send_private_comment_reply(comment_id, text)
    except Exception:  # noqa: BLE001
        logger.exception("dm_private_reply_failed comment_id=%s", comment_id)
        result = {"ok": False, "error": "unexpected_error", "json": None}

    status = "sent" if result.get("ok") else "failed"
    error_text = None if result.get("ok") else str(result.get("error") or result.get("json"))
    db.update_outbox(outbox_id, status, error_text, db.utc_now_iso())
    db.insert_event(
        thread_id=thread_id,
        event_type="dm_out_private_reply",
        message_id=comment_id,
        text=text,
        from_id="page",
        ts=int(time.time()),
    )


__all__ = ["router"]
