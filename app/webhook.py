from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.state import event_store

logger = logging.getLogger("insta-bot")

router = APIRouter()

VERIFY_TOKEN_ENV = "META_VERIFY_TOKEN"
APP_SECRET_ENV = "META_APP_SECRET"
SKIP_SIGNATURE_ENV = "SKIP_SIGNATURE_CHECK"
# Render env vars: set META_VERIFY_TOKEN, META_APP_SECRET, SKIP_SIGNATURE_CHECK
# in the Render dashboard → Environment for this service.
AUTO_REPLY_ENV = "AUTO_REPLY"
IG_ACCESS_TOKEN_ENV = "IG_ACCESS_TOKEN"
IG_BUSINESS_ID_ENV = "IG_BUSINESS_ID"
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v24.0")

AUTO_REPLY_TEMPLATE = "Salam! Məlumat üçün + yazın, sizə ətraflı göndərək."


def get_instagram_access_token() -> str | None:
    return os.getenv(IG_ACCESS_TOKEN_ENV)


def get_instagram_business_id() -> str | None:
    return os.getenv(IG_BUSINESS_ID_ENV)


def reply_to_comment(comment_id: str, message: str, access_token: str) -> dict[str, Any]:
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{comment_id}/replies"
    response = requests.post(
        url,
        params={"access_token": access_token},
        json={"message": message},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def send_instagram_message(recipient_id: str, message: str) -> dict[str, Any]:
    access_token = get_instagram_access_token()
    if not access_token:
        raise ValueError("IG_ACCESS_TOKEN is not configured")
    ig_business_id = get_instagram_business_id()
    if not ig_business_id:
        raise ValueError("IG_BUSINESS_ID is not configured")
    url = (
        f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_business_id}/messages"
    )
    response = requests.post(
        url,
        params={"access_token": access_token},
        json={
            "recipient": {"id": recipient_id},
            "message": {"text": message},
        },
        timeout=20,
    )
    try:
        response.raise_for_status()
    except requests.RequestException:
        logger.exception(
            "Graph API send message failed recipient_id=%s response=%s",
            recipient_id,
            response.text,
        )
        raise
    payload = response.json()
    logger.info(
        "Graph API send message response recipient_id=%s payload=%s",
        recipient_id,
        payload,
    )
    return payload


def set_comment_hidden(
    comment_id: str, hide_bool: bool, access_token: str
) -> dict[str, Any]:
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{comment_id}"
    response = requests.post(
        url,
        params={"hide": str(hide_bool).lower(), "access_token": access_token},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


@router.get("/webhook", response_class=PlainTextResponse, response_model=None)
def verify_webhook(request: Request):
    hub_mode = request.query_params.get("hub.mode")
    hub_verify_token = request.query_params.get("hub.verify_token")
    hub_challenge = request.query_params.get("hub.challenge")
    verify_token = os.getenv(VERIFY_TOKEN_ENV)
    if hub_mode or hub_verify_token or hub_challenge:
        if (
            hub_mode == "subscribe"
            and verify_token
            and hub_verify_token == verify_token
            and hub_challenge
        ):
            return PlainTextResponse(content=hub_challenge, status_code=200)
        return PlainTextResponse(content="Forbidden", status_code=403)
    return JSONResponse(
        content={"status": "ok", "note": "webhook endpoint alive"},
        status_code=200,
    )


@router.head("/webhook")
def webhook_head() -> PlainTextResponse:
    return PlainTextResponse(content="", status_code=200)


@router.post("/webhook")
async def receive_webhook(
    request: Request, background_tasks: BackgroundTasks
) -> JSONResponse:
    return await _handle_webhook(
        request, verify_signature=True, background_tasks=background_tasks
    )


@router.post("/debug/webhook")
async def debug_webhook(
    request: Request, background_tasks: BackgroundTasks
) -> JSONResponse:
    return await _handle_webhook(
        request, verify_signature=False, background_tasks=background_tasks
    )


@router.get("/debug/last_webhook")
def debug_last_webhook() -> JSONResponse:
    payload = event_store.get_last_payload() or {}
    return JSONResponse(content=payload, status_code=200)


async def _handle_webhook(
    request: Request,
    verify_signature: bool,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    try:
        raw_body = await request.body()
        log_request(request, raw_body)
        debug_mode = (
            os.getenv(SKIP_SIGNATURE_ENV, "").lower() in {"1", "true", "yes"}
        )
        signature_header = request.headers.get("X-Hub-Signature-256")
        if verify_signature and not debug_mode:
            if not verify_signature_header(raw_body, signature_header):
                logger.warning("invalid signature")
                return JSONResponse(
                    content={"ok": False, "error": "invalid_signature"},
                    status_code=200,
                )
        if debug_mode:
            logger.info(
                "SKIP_SIGNATURE_CHECK enabled; signature verification bypassed"
            )
        payload = parse_json_payload(raw_body)
        logger.info("webhook payload=%s", payload)
        event_store.set_last_payload(payload)
        event_store.add_webhook_payload(
            {
                "received_at": datetime.now(timezone.utc).isoformat(),
                "payload": payload,
            }
        )
        background_tasks.add_task(process_and_log_payload, payload)
        return JSONResponse(content={"ok": True}, status_code=200)
    except Exception:  # noqa: BLE001
        logger.exception("Webhook processing failed")
        return JSONResponse(
            content={"ok": False, "error": "processing_failed"},
            status_code=200,
        )


def process_and_log_payload(payload: dict[str, Any] | None) -> None:
    try:
        log_payload_summary(payload)
        if payload:
            process_webhook_payload(payload)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to process webhook payload")


def log_request(request: Request, raw_body: bytes) -> None:
    client_ip = request.client.host if request.client else "unknown"
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip() or client_ip
    headers = dict(request.headers)
    body_text = raw_body.decode("utf-8", errors="replace")
    preview = body_text[:500]
    logger.info(
        "webhook request method=%s path=%s client_ip=%s body_length=%s",
        request.method,
        request.url.path,
        client_ip,
        len(raw_body),
    )
    logger.info("webhook headers=%s", headers)
    logger.info("webhook body preview=%s", preview)


def verify_signature_header(raw_body: bytes, signature_header: str | None) -> bool:
    app_secret = os.getenv(APP_SECRET_ENV)
    if not app_secret:
        logger.warning(
            "META_APP_SECRET not configured; skipping signature verification"
        )
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        logger.warning("Missing or invalid signature header")
        return False
    provided = signature_header.split("sha256=", 1)[1]
    expected = hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(provided, expected):
        logger.warning("Signature mismatch")
        return False
    return True


def parse_json_payload(raw_body: bytes) -> dict[str, Any] | None:
    if not raw_body:
        return None
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        logger.exception("Failed to decode webhook payload")
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def log_payload_summary(payload: dict[str, Any] | None) -> None:
    if not payload:
        logger.info("Webhook payload summary: empty")
        return
    entry_count = 0
    entry = payload.get("entry")
    if isinstance(entry, list):
        entry_count = len(entry)
    logger.info(
        "Webhook payload summary: object=%s entry_count=%s",
        payload.get("object"),
        entry_count,
    )


def process_webhook_payload(payload: dict[str, Any]) -> None:
    entries = payload.get("entry") or []
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes") or []
        if isinstance(changes, list):
            for change in changes:
                if not isinstance(change, dict):
                    continue
                if change.get("field") == "comments":
                    handle_comment_change(change.get("value") or {})
        messaging_events = entry.get("messaging") or []
        if isinstance(messaging_events, list):
            for messaging_event in messaging_events:
                if not isinstance(messaging_event, dict):
                    continue
                handle_messaging_event(messaging_event)


def handle_comment_change(value: dict[str, Any]) -> None:
    comment_id = value.get("comment_id") or value.get("id")
    media_id = value.get("media_id")
    text = value.get("text") or value.get("message")
    sender_id = (value.get("from") or {}).get("id")
    timestamp = value.get("timestamp")
    thread_id = sender_id or comment_id
    is_edit = bool(value.get("is_edited")) or value.get("verb") == "edited"
    if is_edit and comment_id:
        original_event = event_store.get_comment(comment_id)
        original_text = original_event.get("text") if original_event else None
        edited_event = {
            "event_type": "comment_edit",
            "received_at": datetime.now(timezone.utc).isoformat(),
            "thread_id": thread_id,
            "comment_id": comment_id,
            "media_id": media_id,
            "original_text": original_text,
            "edited_text": text,
            "preview": text or original_text,
            "from_id": sender_id,
            "timestamp": timestamp,
        }
        event_store.add_event(edited_event)
        if comment_id:
            event_store.register_comment(comment_id, edited_event)
        logger.info("Stored comment edit event: %s", edited_event)
        return
    event = {
        "event_type": "comment",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "thread_id": thread_id,
        "comment_id": comment_id,
        "media_id": media_id,
        "text": text,
        "preview": text,
        "from_id": sender_id,
        "timestamp": timestamp,
    }
    event_store.add_event(event)
    if comment_id:
        event_store.register_comment(comment_id, event)
    logger.info("Stored comment event: %s", event)
    if os.getenv(AUTO_REPLY_ENV) == "1" and comment_id:
        access_token = get_instagram_access_token()
        if not access_token:
            logger.warning("AUTO_REPLY enabled but no access token configured")
            return
        try:
            reply_to_comment(comment_id, AUTO_REPLY_TEMPLATE, access_token)
            logger.info("Auto-replied to comment_id=%s", comment_id)
        except requests.RequestException:
            logger.exception("Failed to auto-reply to comment_id=%s", comment_id)


def handle_messaging_event(event: dict[str, Any]) -> None:
    sender_id = (event.get("sender") or {}).get("id")
    recipient_id = (event.get("recipient") or {}).get("id")
    message = event.get("message") or {}
    message_edit = event.get("message_edit") or {}
    timestamp = event.get("timestamp")
    if message:
        text = message.get("text")
        message_id = message.get("mid") or event.get("message_id")
        thread_id = sender_id or recipient_id
        event_data = {
            "event_type": "message",
            "received_at": datetime.now(timezone.utc).isoformat(),
            "thread_id": thread_id,
            "message_id": message_id,
            "text": text,
            "preview": text,
            "from_id": sender_id,
            "timestamp": timestamp,
        }
        event_store.add_event(event_data)
        if message_id:
            event_store.register_message(message_id, event_data)
        logger.info("Stored message event: %s", event_data)
        return
    if message_edit:
        message_id = message_edit.get("mid") or event.get("message_id")
        edited_text = message_edit.get("text")
        thread_id = sender_id or recipient_id
        original_text = None
        if message_id:
            original_event = event_store.get_message(message_id)
            if original_event:
                original_text = original_event.get("text")
                original_event["edited_text"] = edited_text
                original_event["preview"] = edited_text
        edit_event = {
            "event_type": "message_edit",
            "received_at": datetime.now(timezone.utc).isoformat(),
            "thread_id": thread_id,
            "message_id": message_id,
            "original_text": original_text,
            "edited_text": edited_text,
            "preview": edited_text or original_text,
            "from_id": sender_id,
            "timestamp": timestamp,
        }
        event_store.add_event(edit_event)
        logger.info("Stored message edit event: %s", edit_event)
