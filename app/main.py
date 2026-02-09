from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.db import get_session, init_db
from app.meta import send_comment_reply, send_dm
from app.models import Draft, Message

logger = logging.getLogger("insta-bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DEFAULT_DM_TEMPLATE = (
    "Salam! Mesajınızı aldıq. Ətraflı məlumat üçün zəhmət olmasa yazın."
)
DEFAULT_COMMENT_TEMPLATE = "Salam, ətraflı məlumat üçün DM yazın."

VERIFY_TOKEN_ENV = "VERIFY_TOKEN"
APP_SECRET_ENV = "APP_SECRET"
ADMIN_USER_ENV = "ADMIN_USER"
ADMIN_PASS_ENV = "ADMIN_PASS"

security = HTTPBasic()


class DraftUpdate(BaseModel):
    text: str = Field(..., min_length=1)

app = FastAPI(title="Instagram Messaging Webhook")


@app.middleware("http")
async def log_requests(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    logger.info(
        "request method=%s path=%s query=%s status=%s",
        request.method,
        request.url.path,
        request.url.query,
        response.status_code,
    )
    return response


@app.on_event("startup")
def startup() -> None:
    init_db()


def require_admin(
    credentials: HTTPBasicCredentials = Depends(security),
) -> None:
    admin_user = os.getenv(ADMIN_USER_ENV)
    admin_pass = os.getenv(ADMIN_PASS_ENV)
    if not admin_user or not admin_pass:
        return
    if credentials.username != admin_user or credentials.password != admin_pass:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/")
def root() -> dict[str, str]:
    return {"status": "ok"}


@app.head("/")
def root_head() -> Response:
    return Response(status_code=200)


@app.get("/admin", dependencies=[Depends(require_admin)])
def admin_panel() -> dict[str, str]:
    return {"status": "admin ok"}


@app.head("/admin", dependencies=[Depends(require_admin)])
def admin_panel_head() -> Response:
    return Response(status_code=200)


@app.get("/webhook")
def verify_instagram_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    verify_token = os.getenv(VERIFY_TOKEN_ENV)
    if (
        hub_mode == "subscribe"
        and verify_token
        and hub_verify_token == verify_token
        and hub_challenge
    ):
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(status_code=403, detail="Forbidden")


@app.head("/webhook")
def webhook_head() -> Response:
    return Response(status_code=200)


@app.post("/webhook")
async def receive_instagram_webhook(
    request: Request, background_tasks: BackgroundTasks
) -> JSONResponse:
    raw_body = await request.body()
    payload = parse_json_payload(raw_body)
    if payload is not None:
        logger.info("Webhook payload:\n%s", json.dumps(payload, indent=2, sort_keys=True))
    verify_signature(
        raw_body, request.headers.get("X-Hub-Signature-256"), payload=payload
    )
    background_tasks.add_task(process_webhook_payload, raw_body)
    return JSONResponse(content={"success": True})


@app.get("/admin/drafts/{thread_id}", dependencies=[Depends(require_admin)])
def get_draft(thread_id: str) -> dict[str, Any]:
    with get_session() as session:
        draft = session.query(Draft).filter(Draft.thread_id == thread_id).one_or_none()
        if draft:
            return {
                "thread_id": thread_id,
                "text": draft.text,
                "updated_at": draft.updated_at.isoformat(),
            }
    return {
        "thread_id": thread_id,
        "text": default_template_for_thread(thread_id),
        "updated_at": None,
    }


@app.post("/admin/drafts/{thread_id}", dependencies=[Depends(require_admin)])
def update_draft(thread_id: str, payload: DraftUpdate) -> dict[str, Any]:
    with get_session() as session:
        draft = session.query(Draft).filter(Draft.thread_id == thread_id).one_or_none()
        if draft:
            draft.text = payload.text
        else:
            draft = Draft(thread_id=thread_id, text=payload.text)
            session.add(draft)
        session.flush()
        return {
            "thread_id": thread_id,
            "text": draft.text,
            "updated_at": draft.updated_at.isoformat(),
        }


@app.post("/admin/send/{thread_id}", dependencies=[Depends(require_admin)])
def send_draft(thread_id: str) -> dict[str, Any]:
    with get_session() as session:
        draft = session.query(Draft).filter(Draft.thread_id == thread_id).one_or_none()
        text = draft.text if draft else default_template_for_thread(thread_id)
    if thread_id.startswith("comment:"):
        comment_id = thread_id.split("comment:", 1)[1]
        if not comment_id:
            raise HTTPException(status_code=400, detail="Invalid comment thread id")
        response = send_comment_reply(comment_id, text)
    else:
        recipient_id = thread_id.split("dm:", 1)[1] if thread_id.startswith("dm:") else thread_id
        response = send_dm(recipient_id, text)
    return {"thread_id": thread_id, "sent": True, "response": response}


def verify_signature(
    raw_body: bytes, signature_header: str | None, payload: dict[str, Any] | None
) -> None:
    app_secret = os.getenv(APP_SECRET_ENV)
    if not signature_header or not app_secret:
        if not signature_header:
            logger.info("No X-Hub-Signature-256 header present")
        if not app_secret:
            logger.info("APP_SECRET not configured; skipping signature check")
        return
    if not signature_header.startswith("sha256="):
        raise HTTPException(status_code=403, detail="Invalid signature format")
    provided = signature_header.split("sha256=", 1)[1]
    expected = hmac_sha256(app_secret, raw_body)
    if not hmac.compare_digest(provided, expected):
        logger.warning(
            "Signature mismatch for payload: %s",
            json.dumps(payload, indent=2, sort_keys=True) if payload else "<empty>",
        )
        raise HTTPException(status_code=403, detail="Invalid signature")


def hmac_sha256(secret: str, raw_body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def parse_json_payload(raw_body: bytes) -> dict[str, Any] | None:
    if not raw_body:
        return None
    try:
        return json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError:
        logger.exception("Failed to decode webhook payload")
        return None


@app.get("/debug/routes")
def debug_routes() -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for route in app.routes:
        methods = sorted(route.methods or [])
        routes.append({"path": route.path, "methods": methods, "name": route.name})
    return routes


def process_webhook_payload(raw_body: bytes) -> None:
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        logger.exception("Failed to decode webhook payload")
        return
    try:
        handle_webhook_payload(payload)
    except Exception:
        logger.exception("Failed to handle webhook payload")


def handle_webhook_payload(payload: dict[str, Any]) -> None:
    logger.info("Webhook payload received: %s", payload)
    if payload.get("object") not in {"instagram", "page"}:
        return
    for entry in payload.get("entry", []) or []:
        handle_entry(entry)


def handle_entry(entry: dict[str, Any]) -> None:
    for event in entry.get("messaging", []) or []:
        handle_dm_event(event)
    for change in entry.get("changes", []) or []:
        handle_change_event(change)


def handle_dm_event(event: dict[str, Any]) -> None:
    message = event.get("message") or {}
    text = message.get("text")
    sender_id = event.get("sender", {}).get("id")
    recipient_id = event.get("recipient", {}).get("id")
    timestamp = event.get("timestamp")
    if not sender_id or not recipient_id:
        return
    if text:
        log_event(
            {
                "field": "messages",
                "timestamp": timestamp,
                "sender_id": str(sender_id),
                "recipient_id": str(recipient_id),
                "message_text": str(text),
            }
        )
        store_message(
            {
                "sender_id": str(sender_id),
                "recipient_id": str(recipient_id),
                "timestamp": int(timestamp) if timestamp else 0,
                "text": str(text),
                "event": event,
            }
        )
        create_draft_if_missing(f"dm:{sender_id}", DEFAULT_DM_TEMPLATE)


def handle_change_event(change: dict[str, Any]) -> None:
    field = change.get("field") or "unknown"
    value = change.get("value") or {}
    if field == "comments":
        comment_id = value.get("comment_id") or value.get("id")
        media_id = value.get("media_id")
        text = value.get("text")
        sender_id = (value.get("from") or {}).get("id")
        log_event(
            {
                "field": field,
                "timestamp": value.get("timestamp"),
                "sender_id": sender_id,
                "comment_id": comment_id,
                "media_id": media_id,
                "message_text": text,
            }
        )
        if comment_id:
            create_draft_if_missing(
                f"comment:{comment_id}", DEFAULT_COMMENT_TEMPLATE
            )
    elif field == "mentions":
        log_event(
            {
                "field": field,
                "timestamp": value.get("timestamp"),
                "sender_id": (value.get("from") or {}).get("id"),
                "media_id": value.get("media_id"),
                "message_text": value.get("text"),
            }
        )
    else:
        log_event({"field": field, "timestamp": value.get("timestamp")})


def log_event(data: dict[str, Any]) -> None:
    payload = {
        "timestamp": data.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        "field": data.get("field"),
        "sender_id": data.get("sender_id"),
        "recipient_id": data.get("recipient_id"),
        "message_text": data.get("message_text"),
        "comment_id": data.get("comment_id"),
        "media_id": data.get("media_id"),
    }
    logger.info("Webhook event: %s", payload)


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


def create_draft_if_missing(thread_id: str, default_text: str) -> None:
    with get_session() as session:
        existing = session.query(Draft).filter(Draft.thread_id == thread_id).one_or_none()
        if existing:
            return
        session.add(Draft(thread_id=thread_id, text=default_text))


def default_template_for_thread(thread_id: str) -> str:
    if thread_id.startswith("comment:"):
        return DEFAULT_COMMENT_TEMPLATE
    return DEFAULT_DM_TEMPLATE
