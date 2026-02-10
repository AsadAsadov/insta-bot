from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Request, Response

from app import db
from app.meta_client import send_ig_dm, send_private_comment_reply, send_public_comment_reply

logger = logging.getLogger("insta-bot")
router = APIRouter()


@router.get("/webhook")
async def verify_webhook(request: Request) -> Response:
    mode = request.query_params.get("hub.mode")
    verify_token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge", "")
    expected = os.getenv("META_VERIFY_TOKEN", "")
    if mode == "subscribe" and expected and verify_token == expected:
        return Response(content=challenge, status_code=200, media_type="text/plain")
    return Response(content="forbidden", status_code=403, media_type="text/plain")


@router.head("/webhook")
async def webhook_head() -> Response:
    return Response(status_code=200)


def _verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    secret = os.getenv("META_APP_SECRET", "").strip()
    if not secret:
        logger.info("signature_skipped")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        logger.warning("signature_invalid")
        return False
    provided = signature_header.split("=", 1)[1]
    expected = hmac.new(secret.encode("utf-8"), msg=raw_body, digestmod=hashlib.sha256).hexdigest()
    if hmac.compare_digest(provided, expected):
        logger.info("signature_valid")
        return True
    logger.warning("signature_invalid")
    return False


@router.post("/webhook")
async def receive_webhook(request: Request) -> dict[str, Any]:
    raw_body = await request.body()
    if not _verify_signature(raw_body, request.headers.get("X-Hub-Signature-256")):
        return {"ok": True, "ignored": "invalid_signature"}

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        logger.warning("webhook_received parse_error=true")
        return {"ok": True}

    logger.info("webhook_received object=%s", payload.get("object"))
    if payload.get("object") != "instagram":
        return {"ok": True}

    for entry in payload.get("entry", []):
        recipient_igid = str(entry.get("id") or "")
        for item in entry.get("messaging", []):
            _handle_messaging(item, recipient_igid)
        for change in entry.get("changes", []):
            if change.get("field") == "comments":
                _handle_comment_change(change)

    return {"ok": True}


def _handle_messaging(item: dict[str, Any], recipient_igid: str) -> None:
    message = item.get("message") or {}
    text = (message.get("text") or "").strip()
    if not text:
        return

    sender = item.get("sender") or {}
    thread_id = str(sender.get("id") or "").strip()
    if not thread_id:
        return

    message_id = message.get("mid")
    ts = int(item.get("timestamp") or time.time())
    logger.info("webhook_received thread_id=%s recipient_igid=%s", thread_id, recipient_igid)
    db.upsert_thread(thread_id=thread_id, last_message=text, last_ts=ts)
    db.insert_event(thread_id, "message_in", message_id, text, thread_id, ts)

    matched = db.find_first_matching_template(text)
    if not matched:
        return

    reply_text = matched["reply_text"]
    outbox_id = db.create_outbox(thread_id, reply_text)
    send_result = send_ig_dm(thread_id, reply_text)
    status = "sent" if send_result.get("ok") else "failed"
    error = None if send_result.get("ok") else str(send_result.get("error") or send_result.get("json"))
    db.update_outbox(outbox_id, status, error, db.utc_now_iso())
    db.insert_event(
        thread_id=thread_id,
        event_type="message_out",
        message_id=(send_result.get("json") or {}).get("message_id") if isinstance(send_result.get("json"), dict) else None,
        text=reply_text,
        from_id="bot",
        ts=int(time.time()),
    )
    db.upsert_thread(thread_id=thread_id, last_message=reply_text, last_ts=int(time.time()))


def _handle_comment_change(change: dict[str, Any]) -> None:
    value = change.get("value") or {}
    comment_id = str(value.get("id") or "")
    text = (value.get("text") or "").strip()
    from_id = str((value.get("from") or {}).get("id") or "")
    if not (comment_id and text and from_id):
        return

    db.upsert_thread(from_id, text, int(time.time()))
    db.insert_event(from_id, "comment_in", comment_id, text, from_id, int(time.time()))
    trigger = db.find_first_matching_comment_trigger(text)
    if not trigger:
        return

    public_result = send_public_comment_reply(comment_id, trigger["public_reply_text"])
    db.insert_event(
        from_id,
        "comment_public_reply",
        (public_result.get("json") or {}).get("id") if isinstance(public_result.get("json"), dict) else None,
        trigger["public_reply_text"],
        "bot",
        int(time.time()),
    )
    private_result = send_private_comment_reply(comment_id, trigger["dm_reply_text"])
    db.insert_event(
        from_id,
        "dm_out_private_reply",
        (private_result.get("json") or {}).get("message_id") if isinstance(private_result.get("json"), dict) else None,
        trigger["dm_reply_text"],
        "bot",
        int(time.time()),
    )
