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

VERIFY_TOKEN_ENV = "VERIFY_TOKEN"
APP_SECRET_ENV = "APP_SECRET"
AUTO_REPLY_ENV = "AUTO_REPLY"
GRAPH_TOKEN_ENV = "GRAPH_TOKEN"
IG_PAGE_ACCESS_TOKEN_ENV = "IG_PAGE_ACCESS_TOKEN"
PAGE_ACCESS_TOKEN_ENV = "PAGE_ACCESS_TOKEN"
IG_BUSINESS_ACCOUNT_ENV = "IG_BUSINESS_ACCOUNT_ID"
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v20.0")

AUTO_REPLY_TEMPLATE = "Salam! Məlumat üçün + yazın, sizə ətraflı göndərək."


def get_access_token() -> str | None:
    return (
        os.getenv(IG_PAGE_ACCESS_TOKEN_ENV)
        or os.getenv(PAGE_ACCESS_TOKEN_ENV)
        or os.getenv(GRAPH_TOKEN_ENV)
    )


def reply_to_comment(comment_id: str, message: str, access_token: str) -> dict[str, Any]:
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{comment_id}/replies"
    response = requests.post(
        url,
        params={"message": message, "access_token": access_token},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def send_dm_reply(
    recipient_id: str, message: str, access_token: str
) -> dict[str, Any]:
    ig_business_account_id = os.getenv(IG_BUSINESS_ACCOUNT_ENV)
    if not ig_business_account_id:
        raise ValueError("IG_BUSINESS_ACCOUNT_ID is not configured")
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{ig_business_account_id}/messages"
    response = requests.post(
        url,
        params={"access_token": access_token},
        json={
            "recipient": {"id": recipient_id},
            "message": {"text": message},
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


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


@router.get("/webhook")
def verify_webhook(
    request: Request,
) -> PlainTextResponse:
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
    raw_body = await request.body()
    log_request(request, raw_body)
    if verify_signature and not verify_signature_header(
        raw_body, request.headers.get("X-Hub-Signature-256")
    ):
        return JSONResponse(content={"ok": False}, status_code=403)
    payload = parse_json_payload(raw_body)
    event_store.set_last_payload(payload)
    background_tasks.add_task(process_and_log_payload, payload)
    return JSONResponse(content={"ok": True}, status_code=200)


def process_and_log_payload(payload: dict[str, Any] | None) -> None:
    log_payload_summary(payload)
    if payload:
        process_webhook_payload(payload)


def log_request(request: Request, raw_body: bytes) -> None:
    client_ip = request.client.host if request.client else "unknown"
    headers = dict(request.headers)
    logger.info(
        "webhook request method=%s path=%s client_ip=%s",
        request.method,
        request.url.path,
        client_ip,
    )
    logger.info("webhook headers=%s", headers)
    logger.info(
        "webhook raw_body=%s", raw_body.decode("utf-8", errors="replace")
    )


def verify_signature_header(raw_body: bytes, signature_header: str | None) -> bool:
    app_secret = os.getenv(APP_SECRET_ENV)
    if not app_secret:
        logger.info("APP_SECRET not configured; skipping signature verification")
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
    logger.info("Stored comment event: %s", event)
    if os.getenv(AUTO_REPLY_ENV) == "1" and comment_id:
        access_token = get_access_token()
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
