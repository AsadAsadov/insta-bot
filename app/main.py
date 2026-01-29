from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.db import get_session, init_db, set_setting
from app.llm import generate_reply
from app.meta import send_dm
from app.models import HandoffFlag, Keyword, MessageLog, User

VERIFY_TOKEN = os.getenv("IG_WEBHOOK_VERIFY_TOKEN", "change-me")

app = FastAPI(title="Insta Bot Web Service")

TEMPLATES = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def healthcheck() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/webhook")
def verify_instagram_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    if hub_mode != "subscribe":
        raise HTTPException(status_code=400, detail="Invalid mode")
    if hub_verify_token != VERIFY_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid verify token")
    if not hub_challenge:
        raise HTTPException(status_code=400, detail="Missing challenge")
    return HTMLResponse(content=hub_challenge)


@app.post("/webhook")
async def receive_instagram_webhook(request: Request) -> JSONResponse:
    payload = await request.json()
    await handle_webhook_payload(payload)
    return JSONResponse(content={"status": "received"})


@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    with get_session() as session:
        keywords = session.execute(select(Keyword).order_by(Keyword.keyword)).scalars().all()
        logs = (
            session.execute(
                select(MessageLog)
                .order_by(MessageLog.created_at.desc())
                .limit(100)
            )
            .scalars()
            .all()
        )
        auto_reply_enabled = get_auto_reply_setting(session)
    return TEMPLATES.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "keywords": keywords,
            "logs": logs,
            "auto_reply_enabled": auto_reply_enabled,
        },
    )


@app.post("/admin/keywords")
def add_keyword(keyword: str = Form(...)) -> RedirectResponse:
    cleaned = keyword.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Keyword required")
    with get_session() as session:
        existing = session.execute(select(Keyword).where(Keyword.keyword == cleaned)).scalar_one_or_none()
        if existing is None:
            session.add(Keyword(keyword=cleaned))
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/keywords/{keyword_id}/toggle")
def toggle_keyword(keyword_id: int) -> RedirectResponse:
    with get_session() as session:
        keyword = session.get(Keyword, keyword_id)
        if keyword is None:
            raise HTTPException(status_code=404, detail="Keyword not found")
        keyword.is_active = not keyword.is_active
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/keywords/{keyword_id}/delete")
def delete_keyword(keyword_id: int) -> RedirectResponse:
    with get_session() as session:
        keyword = session.get(Keyword, keyword_id)
        if keyword is None:
            raise HTTPException(status_code=404, detail="Keyword not found")
        session.delete(keyword)
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/auto-reply")
def toggle_auto_reply(enabled: bool = Form(...)) -> RedirectResponse:
    with get_session() as session:
        set_setting(session, "auto_reply_enabled", "true" if enabled else "false")
    return RedirectResponse(url="/admin", status_code=303)


async def handle_webhook_payload(payload: Dict[str, Any]) -> None:
    if payload.get("object") not in {"instagram", "page"}:
        return
    for entry in payload.get("entry", []):
        messaging_events = entry.get("messaging", []) or []
        changes = entry.get("changes", []) or []
        for event in messaging_events:
            await handle_message_event(event)
        for change in changes:
            await handle_comment_event(change)


async def handle_message_event(event: Dict[str, Any]) -> None:
    message = event.get("message") or {}
    text = message.get("text")
    if not text:
        return
    sender_id = event.get("sender", {}).get("id")
    if not sender_id:
        return
    with get_session() as session:
        user = get_or_create_user(session, sender_id)
        log_message(session, user.id, "incoming", text, "direct_message", event)
        if should_skip_auto_reply(session, user.id, text):
            return
        reply_text = generate_reply(text)
        send_dm(sender_id, reply_text)
        log_message(session, user.id, "outgoing", reply_text, "direct_message_reply", {})


async def handle_comment_event(change: Dict[str, Any]) -> None:
    if change.get("field") != "comments":
        return
    value = change.get("value") or {}
    text = value.get("text")
    from_user = value.get("from") or {}
    sender_id = from_user.get("id")
    if not text or not sender_id:
        return
    with get_session() as session:
        user = get_or_create_user(session, sender_id, from_user.get("username"))
        log_message(session, user.id, "incoming", text, "comment", change)
        if should_skip_auto_reply(session, user.id, text):
            return
        reply_text = generate_reply(text)
        send_dm(sender_id, reply_text)
        log_message(session, user.id, "outgoing", reply_text, "comment_reply", {})


def get_or_create_user(session, ig_user_id: str, username: Optional[str] = None) -> User:
    user = session.execute(select(User).where(User.ig_user_id == ig_user_id)).scalar_one_or_none()
    if user is None:
        user = User(ig_user_id=ig_user_id, username=username)
        session.add(user)
        session.flush()
    elif username and user.username != username:
        user.username = username
    return user


def log_message(
    session,
    user_id: int | None,
    direction: str,
    text: str,
    event_type: str,
    payload: Dict[str, Any],
) -> None:
    session.add(
        MessageLog(
            user_id=user_id,
            direction=direction,
            text=text,
            event_type=event_type,
            raw_payload=json.loads(json.dumps(payload)),
        )
    )


def should_skip_auto_reply(session, user_id: int, text: str) -> bool:
    if not get_auto_reply_setting(session):
        return True
    handoff = session.execute(
        select(HandoffFlag).where(HandoffFlag.user_id == user_id, HandoffFlag.is_handoff.is_(True))
    ).scalar_one_or_none()
    if handoff is not None:
        return True
    keywords = session.execute(select(Keyword).where(Keyword.is_active.is_(True))).scalars().all()
    if not keywords:
        return False
    lowered = text.lower()
    return not any(keyword.keyword.lower() in lowered for keyword in keywords)


def get_auto_reply_setting(session) -> bool:
    from app.db import get_setting

    value = get_setting(session, "auto_reply_enabled", "true")
    return value == "true"
