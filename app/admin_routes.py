from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from app import db
from app.meta_client import send_ig_dm

logger = logging.getLogger("insta-bot")

security = HTTPBasic(auto_error=False)
templates = Jinja2Templates(directory="app/templates")


def _get_admin_credentials() -> tuple[str, str] | None:
    user = os.getenv("ADMIN_USER")
    password = os.getenv("ADMIN_PASS")
    if not user or not password:
        return None
    return user, password


def require_admin(credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
    stored = _get_admin_credentials()
    if not stored:
        return
    if credentials is None:
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})
    user, password = stored
    if not (
        secrets.compare_digest(credentials.username, user)
        and secrets.compare_digest(credentials.password, password)
    ):
        raise HTTPException(status_code=401, detail="Unauthorized", headers={"WWW-Authenticate": "Basic"})


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _base_context(request: Request) -> dict[str, Any]:
    return {
        "request": request,
        "meta_missing": not os.getenv("META_PAGE_ACCESS_TOKEN"),
        "flash": request.query_params.get("flash"),
        "flash_type": request.query_params.get("flash_type", "info"),
    }


@router.get("")
def admin_index(request: Request):
    threads = [db.row_to_dict(row) for row in db.list_threads()]
    return templates.TemplateResponse(
        "admin_index.html",
        {**_base_context(request), "threads": threads, "active_thread": None},
    )


@router.get("/thread/{thread_id}")
def thread_detail(request: Request, thread_id: str):
    threads = [db.row_to_dict(row) for row in db.list_threads()]
    events = [db.row_to_dict(row) for row in db.get_thread_events(thread_id)]
    template_list = [db.row_to_dict(row) for row in db.list_templates()]
    outbox_entries = [db.row_to_dict(row) for row in db.list_outbox(thread_id)]
    latest_failed = db.row_to_dict(db.get_latest_failed_outbox(thread_id))
    return templates.TemplateResponse(
        "thread.html",
        {
            **_base_context(request),
            "threads": threads,
            "active_thread": thread_id,
            "events": events,
            "templates": template_list,
            "outbox_entries": outbox_entries,
            "latest_failed": latest_failed,
        },
    )


@router.post("/message-reply")
def admin_message_reply(
    thread_id: str = Form(...),
    message_text: str = Form(...),
):
    trimmed = message_text.strip()
    if not trimmed:
        return RedirectResponse(
            url=f"/admin/thread/{thread_id}?flash=Message+cannot+be+empty&flash_type=warning",
            status_code=303,
        )

    outbox_id = db.create_outbox(thread_id, trimmed)
    result = send_ig_dm(thread_id, trimmed)
    status = "sent" if result.get("ok") else "failed"
    error_text = None if result.get("ok") else str(result.get("error") or result.get("json"))
    db.update_outbox(outbox_id, status, error_text, db.utc_now_iso())
    db.insert_event(
        thread_id=thread_id,
        event_type="message_out",
        message_id=(result.get("json") or {}).get("message_id") if isinstance(result.get("json"), dict) else None,
        text=trimmed,
        from_id="page",
        ts=int(time.time()),
    )
    db.upsert_thread(thread_id, trimmed, int(time.time()))
    level = "success" if result.get("ok") else "danger"
    message = "Reply sent" if result.get("ok") else "Reply failed"
    return RedirectResponse(
        url=f"/admin/thread/{thread_id}?flash={message}&flash_type={level}",
        status_code=303,
    )


@router.get("/templates")
def templates_page(request: Request):
    template_list = [db.row_to_dict(row) for row in db.list_templates()]
    return templates.TemplateResponse(
        "templates.html",
        {**_base_context(request), "templates": template_list},
    )


@router.post("/templates")
def create_template(
    name: str = Form(...),
    trigger_type: str = Form(...),
    trigger_value: str = Form(""),
    reply_text: str = Form(...),
    is_active: int = Form(0),
):
    db.create_template(name, trigger_type, trigger_value, reply_text, is_active)
    return RedirectResponse("/admin/templates?flash=Template+created&flash_type=success", status_code=303)


@router.post("/templates/{template_id}/toggle")
def toggle_template(template_id: int):
    db.toggle_template(template_id)
    return RedirectResponse("/admin/templates?flash=Template+updated&flash_type=info", status_code=303)


@router.post("/templates/{template_id}/delete")
def delete_template(template_id: int):
    db.delete_template(template_id)
    return RedirectResponse("/admin/templates?flash=Template+deleted&flash_type=warning", status_code=303)


@router.get("/triggers")
def comment_triggers_page(request: Request):
    triggers = [db.row_to_dict(row) for row in db.list_comment_triggers()]
    return templates.TemplateResponse(
        "triggers.html",
        {**_base_context(request), "triggers": triggers},
    )


@router.post("/triggers")
def create_comment_trigger(
    name: str = Form(...),
    trigger_type: str = Form(...),
    trigger_value: str = Form(""),
    public_reply_text: str = Form(...),
    dm_reply_text: str = Form(...),
    is_active: int = Form(0),
):
    db.create_comment_trigger(
        name=name,
        trigger_type=trigger_type,
        trigger_value=trigger_value,
        public_reply_text=public_reply_text,
        dm_reply_text=dm_reply_text,
        is_active=is_active,
    )
    return RedirectResponse("/admin/triggers?flash=Trigger+created&flash_type=success", status_code=303)


@router.post("/triggers/{trigger_id}/toggle")
def toggle_comment_trigger(trigger_id: int):
    db.toggle_comment_trigger(trigger_id)
    return RedirectResponse("/admin/triggers?flash=Trigger+updated&flash_type=info", status_code=303)


@router.post("/triggers/{trigger_id}/delete")
def delete_comment_trigger(trigger_id: int):
    db.delete_comment_trigger(trigger_id)
    return RedirectResponse("/admin/triggers?flash=Trigger+deleted&flash_type=warning", status_code=303)


__all__ = ["router"]
