from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.db import get_session, init_db
from app.meta import send_dm
from app.models import Message

logger = logging.getLogger("insta-bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
APP_SECRET = os.getenv("APP_SECRET")

AUTO_REPLY_TEXT = (
    "Salam! Mesajınız qəbul olundu. Ətraflı məlumat üçün nömrənizi yazın."
)

app = FastAPI(title="Instagram Messaging Webhook")


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def healthcheck() -> dict[str, bool]:
    return {"ok": True}


@app.get("/webhook")
def verify_instagram_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    if hub_mode != "subscribe":
        raise HTTPException(status_code=400, detail="Invalid mode")
    if not VERIFY_TOKEN or hub_verify_token != VERIFY_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid verify token")
    if not hub_challenge:
        raise HTTPException(status_code=400, detail="Missing challenge")
    return PlainTextResponse(content=hub_challenge)


@app.post("/webhook")
async def receive_instagram_webhook(request: Request) -> JSONResponse:
    raw_body = await request.body()
    verify_signature(raw_body, request.headers.get("X-Hub-Signature-256"))
    payload = json.loads(raw_body.decode("utf-8") or "{}")
    logger.info("Webhook payload: %s", payload)
    messages = extract_dm_messages(payload)
    for message in messages:
        store_message(message)
        try:
            send_dm(message["sender_id"], AUTO_REPLY_TEXT)
        except Exception:
            logger.exception("Failed to send auto-reply to %s", message["sender_id"])
    return JSONResponse(content={"status": "received"})


def verify_signature(raw_body: bytes, signature_header: str | None) -> None:
    if not APP_SECRET:
        raise HTTPException(status_code=500, detail="APP_SECRET is not configured")
    if not signature_header:
        raise HTTPException(status_code=403, detail="Missing signature")
    if not signature_header.startswith("sha256="):
        raise HTTPException(status_code=403, detail="Invalid signature format")
    provided = signature_header.split("sha256=", 1)[1]
    digest = hmac.new(APP_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided, digest):
        raise HTTPException(status_code=403, detail="Invalid signature")


def extract_dm_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if payload.get("object") not in {"instagram", "page"}:
        return messages
    for entry in payload.get("entry", []):
        for event in entry.get("messaging", []) or []:
            message = event.get("message") or {}
            text = message.get("text")
            if not text:
                continue
            sender_id = event.get("sender", {}).get("id")
            recipient_id = event.get("recipient", {}).get("id")
            timestamp = event.get("timestamp")
            if not sender_id or not recipient_id or timestamp is None:
                continue
            messages.append(
                {
                    "sender_id": str(sender_id),
                    "recipient_id": str(recipient_id),
                    "timestamp": int(timestamp),
                    "text": str(text),
                    "event": event,
                }
            )
    return messages


def store_message(message: dict[str, Any]) -> None:
    with get_session() as session:
        session.add(
            Message(
                sender_id=message["sender_id"],
                recipient_id=message["recipient_id"],
                ts=message["timestamp"],
                text=message["text"],
                raw_json=message["event"],
            )
        )
