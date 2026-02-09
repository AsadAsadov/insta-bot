from __future__ import annotations

import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

import db
from meta_client import send_ig_dm

logger = logging.getLogger("insta-bot")

security = HTTPBasic(auto_error=False)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_templates = Jinja2Templates(directory=str(TEMPLATE_DIR)) if TEMPLATE_DIR.exists() else None


def _render_template(template_name: str, context: dict[str, Any]) -> HTMLResponse:
    template_path = TEMPLATE_DIR / template_name
    if _templates and template_path.exists():
        return _templates.TemplateResponse(template_name, context)
    fallback = f"""
    <html>
        <head><title>Admin</title></head>
        <body>
            <h1>Admin</h1>
            <p>Template <strong>{template_name}</strong> not found.</p>
        </body>
    </html>
    """
    return HTMLResponse(content=fallback, status_code=200)


def _get_admin_credentials() -> tuple[str, str] | None:
    admin_user = os.getenv("ADMIN_USER")
    admin_pass = os.getenv("ADMIN_PASS")
    if not admin_user or not admin_pass:
        return None
    return admin_user, admin_pass


def require_admin(
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> None:
    stored = _get_admin_credentials()
    if not stored:
        return
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    admin_user, admin_pass = stored
    if not (
        secrets.compare_digest(credentials.username, admin_user)
        and secrets.compare_digest(credentials.password, admin_pass)
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)


def _base_context(request: Request) -> dict[str, Any]:
    meta_missing = not os.getenv("META_PAGE_ACCESS_TOKEN")
    return {
        "request": request,
        "meta_missing": meta_missing,
        "flash": request.query_params.get("flash"),
        "flash_type": request.query_params.get("flash_type", "info"),
    }


@router.get("")
def admin_index(request: Request):
    threads = [db.row_to_dict(row) for row in db.list_threads()]
    context = {
        **_base_context(request),
        "threads": threads,
        "active_thread": None,
    }
    return _render_template("admin_index.html", context)


@router.get("/thread/{thread_id}")
def thread_detail(request: Request, thread_id: str):
    threads = [db.row_to_dict(row) for row in db.list_threads()]
    events = [db.row_to_dict(row) for row in db.get_thread_events(thread_id)]
    template_list = [db.row_to_dict(row) for row in db.list_templates()]
    outbox_entries = [db.row_to_dict(row) for row in db.list_outbox(thread_id)]
    latest_failed = db.get_latest_failed_outbox(thread_id)
    context = {
        **_base_context(request),
        "threads": threads,
        "active_thread": thread_id,
        "events": events,
        "templates": template_list,
        "outbox_entries": outbox_entries,
        "latest_failed": db.row_to_dict(latest_failed),
    }
    return _render_template("thread.html", context)


@router.post("/message-reply")
def admin_message_reply(
    request: Request, thread_id: str = Form(...), text: str = Form(...)
):
    trimmed = text.strip()
    if not trimmed:
        return RedirectResponse(
            url=f"/admin/thread/{thread_id}?flash=Message+cannot+be+empty&flash_type=warning",
            status_code=303,
        )
    outbox_id = db.create_outbox(thread_id, trimmed)
    result = send_ig_dm(thread_id, trimmed)
    status = "sent" if result.get("ok") else "failed"
    error_text = None
    if not result.get("ok"):
        error_text = str(result.get("error") or result.get("json"))
    db.update_outbox(outbox_id, status, error_text, db.utc_now_iso())
    db.insert_event(
        thread_id=thread_id,
        event_type="message_out",
        message_id=(result.get("json") or {}).get("message_id")
        if isinstance(result.get("json"), dict)
        else None,
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
    templates_list = [db.row_to_dict(row) for row in db.list_templates()]
    context = {**_base_context(request), "templates": templates_list}
    return _render_template("templates.html", context)


@router.post("/templates")
def create_template(
    request: Request,
    name: str = Form(...),
    trigger_type: str = Form(...),
    trigger_value: str = Form(""),
    reply_text: str = Form(...),
    is_active: int = Form(0),
):
    db.create_template(name, trigger_type, trigger_value, reply_text, is_active)
    return RedirectResponse(
        url="/admin/templates?flash=Template+created&flash_type=success",
        status_code=303,
    )


@router.post("/templates/{template_id}/toggle")
def toggle_template(template_id: int):
    db.toggle_template(template_id)
    return RedirectResponse(
        url="/admin/templates?flash=Template+updated&flash_type=info",
        status_code=303,
    )


@router.post("/templates/{template_id}/delete")
def delete_template(template_id: int):
    db.delete_template(template_id)
    return RedirectResponse(
        url="/admin/templates?flash=Template+deleted&flash_type=warning",
        status_code=303,
    )


__all__ = ["router"]
