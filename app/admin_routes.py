from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import db
from app.meta_client import send_ig_dm

router = APIRouter(prefix="/admin", tags=["admin"])

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _flash(request: Request) -> dict[str, str]:
    return {
        "flash": request.query_params.get("flash", ""),
        "flash_type": request.query_params.get("flash_type", "info"),
    }


def _base_context(request: Request) -> dict:
    return {
        "request": request,
        "token_configured": bool(os.getenv("META_PAGE_ACCESS_TOKEN", "").strip()),
        **_flash(request),
    }


@router.get("")
def admin_index(request: Request):
    thread_rows = [db.row_to_dict(r) for r in db.list_threads()]
    return templates.TemplateResponse(
        "admin_index.html",
        {
            **_base_context(request),
            "threads": thread_rows,
            "selected_thread": None,
            "events": [],
            "last_outbox": None,
            "quick_replies": [r["reply_text"] for r in db.list_templates()],
        },
    )


@router.get("/thread/{thread_id}")
def admin_thread(request: Request, thread_id: str):
    thread_rows = [db.row_to_dict(r) for r in db.list_threads()]
    event_rows = [db.row_to_dict(r) for r in db.get_thread_events(thread_id)]
    last_outbox_row = db.get_latest_outbox_for_thread(thread_id)
    last_outbox = db.row_to_dict(last_outbox_row) if last_outbox_row else None

    return templates.TemplateResponse(
        "thread.html",
        {
            **_base_context(request),
            "threads": thread_rows,
            "selected_thread": thread_id,
            "events": event_rows,
            "last_outbox": last_outbox,
            "quick_replies": [r["reply_text"] for r in db.list_templates()],
        },
    )


@router.post("/message-reply")
def reply_message(thread_id: str = Form(...), text: str = Form(...)):
    trimmed = text.strip()
    if not trimmed:
        return RedirectResponse(
            url=f"/admin/thread/{thread_id}?flash=Message+cannot+be+empty&flash_type=warning",
            status_code=303,
        )

    outbox_id = db.create_outbox(thread_id, trimmed)
    result = send_ig_dm(thread_id, trimmed)
    status = "sent" if result.get("ok") else "failed"
    error = None if result.get("ok") else str(result.get("error") or result.get("json"))
    db.update_outbox(outbox_id, status, error, db.utc_now_iso())
    db.insert_event(
        thread_id=thread_id,
        event_type="message_out",
        message_id=(result.get("json") or {}).get("message_id") if isinstance(result.get("json"), dict) else None,
        text=trimmed,
        from_id="admin",
        ts=int(time.time()),
    )
    db.upsert_thread(thread_id, trimmed, int(time.time()))

    if result.get("ok"):
        return RedirectResponse(
            url=f"/admin/thread/{thread_id}?flash=Reply+sent&flash_type=success",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/admin/thread/{thread_id}?flash=Reply+failed&flash_type=danger",
        status_code=303,
    )


@router.get("/templates")
def list_templates_page(request: Request):
    return templates.TemplateResponse(
        "templates.html",
        {**_base_context(request), "templates_list": [db.row_to_dict(r) for r in db.list_templates()]},
    )


@router.post("/templates")
def create_template(
    name: str = Form(...),
    trigger_type: str = Form(...),
    trigger_value: str = Form(""),
    reply_text: str = Form(...),
    is_active: str | None = Form(None),
):
    db.create_template(name, trigger_type, trigger_value, reply_text, 1 if is_active else 0)
    return RedirectResponse(url="/admin/templates?flash=Template+created&flash_type=success", status_code=303)


@router.post("/templates/{template_id}/toggle")
def toggle_template(template_id: int):
    db.toggle_template(template_id)
    return RedirectResponse(url="/admin/templates?flash=Template+toggled&flash_type=info", status_code=303)


@router.post("/templates/{template_id}/delete")
def delete_template(template_id: int):
    db.delete_template(template_id)
    return RedirectResponse(url="/admin/templates?flash=Template+deleted&flash_type=warning", status_code=303)


@router.get("/posts")
def posts_stub(request: Request):
    oauth_enabled = os.getenv("META_OAUTH_ENABLED", "0") == "1"
    return templates.TemplateResponse(
        "posts.html",
        {**_base_context(request), "oauth_enabled": oauth_enabled},
    )
