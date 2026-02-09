from __future__ import annotations

import json
import os
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.state import event_store
from app.webhook import (
    get_instagram_access_token,
    reply_to_comment,
    send_instagram_message,
    set_comment_hidden,
)

security = HTTPBasic()

DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASS = "admin"


def require_admin(
    credentials: HTTPBasicCredentials = Depends(security),
) -> None:
    admin_user = os.getenv("ADMIN_USER")
    admin_pass = os.getenv("ADMIN_PASS")
    app_env = os.getenv("APP_ENV")
    if not admin_user or not admin_pass:
        if app_env == "development":
            admin_user = DEFAULT_ADMIN_USER
            admin_pass = DEFAULT_ADMIN_PASS
        else:
            raise HTTPException(
                status_code=401,
                detail="Unauthorized",
                headers={"WWW-Authenticate": "Basic"},
            )
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


@router.get("", response_class=HTMLResponse)
def admin_panel() -> HTMLResponse:
    return HTMLResponse(render_admin_page())


@router.get(
    "/drafts/{thread_id}", response_class=JSONResponse
)
def get_draft(thread_id: str) -> JSONResponse:
    draft = event_store.get_draft(thread_id)
    return JSONResponse(content={"thread_id": thread_id, "draft": draft})


@router.post(
    "/drafts/{thread_id}", response_class=HTMLResponse
)
def save_draft(thread_id: str, draft: str = Form(...)) -> HTMLResponse:
    event_store.set_draft(thread_id, draft)
    return HTMLResponse(render_admin_page(f"Draft saved for thread {thread_id}."))


@router.post(
    "/send/{thread_id}", response_class=HTMLResponse
)
def send_draft(thread_id: str) -> HTMLResponse:
    draft = event_store.get_draft(thread_id)
    if not draft.strip():
        return HTMLResponse(
            render_admin_page("Draft is empty."), status_code=400
        )
    access_token = get_instagram_access_token()
    if not access_token:
        return HTMLResponse(
            render_admin_page("Access token not configured."),
            status_code=400,
        )
    try:
        result = send_instagram_message(thread_id, draft)
    except Exception as exc:  # noqa: BLE001
        return HTMLResponse(
            render_admin_page(f"Send failed: {exc}"),
            status_code=500,
        )
    event_store.clear_draft(thread_id)
    return HTMLResponse(render_admin_page(f"Draft sent: {result}"))


@router.post("/reply", response_class=HTMLResponse)
def admin_reply(comment_id: str = Form(...), message: str = Form(...)) -> HTMLResponse:
    access_token = get_instagram_access_token()
    if not access_token:
        return HTMLResponse(
            render_admin_page("Access token not configured."),
            status_code=400,
        )
    try:
        result = reply_to_comment(comment_id, message, access_token)
    except Exception as exc:  # noqa: BLE001
        return HTMLResponse(
            render_admin_page(f"Reply failed: {exc}"),
            status_code=500,
        )
    return HTMLResponse(render_admin_page(f"Reply sent: {result}"))


@router.post(
    "/message-reply", response_class=HTMLResponse
)
def admin_message_reply(
    thread_id: str = Form(...), message: str = Form(...)
) -> HTMLResponse:
    access_token = get_instagram_access_token()
    if not access_token:
        return HTMLResponse(
            render_admin_page("Access token not configured."),
            status_code=400,
        )
    try:
        result = send_instagram_message(thread_id, message)
    except Exception as exc:  # noqa: BLE001
        return HTMLResponse(
            render_admin_page(f"DM reply failed: {exc}"),
            status_code=500,
        )
    return HTMLResponse(render_admin_page(f"DM reply sent: {result}"))


@router.post("/hide", response_class=HTMLResponse)
def admin_hide(comment_id: str = Form(...), hide: str = Form(...)) -> HTMLResponse:
    access_token = get_instagram_access_token()
    if not access_token:
        return HTMLResponse(
            render_admin_page("Access token not configured."),
            status_code=400,
        )
    hide_bool = hide.lower() == "true"
    try:
        result = set_comment_hidden(comment_id, hide_bool, access_token)
    except Exception as exc:  # noqa: BLE001
        return HTMLResponse(
            render_admin_page(f"Hide/unhide failed: {exc}"),
            status_code=500,
        )
    return HTMLResponse(render_admin_page(f"Hide/unhide result: {result}"))


def render_admin_page(message: str | None = None) -> str:
    verify_token_set = "yes" if os.getenv("META_VERIFY_TOKEN") else "no"
    app_secret_set = "yes" if os.getenv("META_APP_SECRET") else "no"
    skip_signature = os.getenv("SKIP_SIGNATURE_CHECK")
    events = event_store.recent(50)
    webhook_payloads = event_store.recent_webhook_payloads(20)
    request_logs = event_store.recent_request_logs(20)
    threads = event_store.list_threads()
    thread_rows = "\n".join(
        _render_thread_row(thread, event_store.get_draft(thread["thread_id"]))
        for thread in threads
    )
    event_rows = "\n".join(_render_event_row(event) for event in events)
    payload_rows = "\n".join(
        _render_payload_row(payload) for payload in webhook_payloads
    )
    request_rows = "\n".join(
        _render_request_row(entry) for entry in request_logs
    )
    message_html = (
        f"<p><strong>{message}</strong></p>" if message else ""
    )
    return f"""
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Webhook Admin</title>
    <style>
      body {{ font-family: Arial, sans-serif; margin: 2rem; }}
      table {{ border-collapse: collapse; width: 100%; }}
      th, td {{ border: 1px solid #ddd; padding: 8px; }}
      th {{ background: #f4f4f4; text-align: left; }}
      form {{ margin-bottom: 1.5rem; }}
      input, select, textarea {{ margin-right: 0.5rem; }}
      textarea {{ width: 100%; min-height: 70px; }}
      .thread-card {{ border: 1px solid #ddd; padding: 1rem; margin-bottom: 1rem; }}
      .thread-meta {{ font-size: 0.9rem; color: #555; }}
      .actions form {{ display: inline-block; margin-right: 0.5rem; }}
    </style>
  </head>
  <body>
    <h1>Webhook Admin</h1>
    <section>
      <p>META_VERIFY_TOKEN set: <strong>{verify_token_set}</strong></p>
      <p>META_APP_SECRET set: <strong>{app_secret_set}</strong></p>
      <p>SKIP_SIGNATURE_CHECK: <strong>{skip_signature or "not set"}</strong></p>
      <p>
        Render environment variables:
        <code>META_VERIFY_TOKEN</code>,
        <code>META_APP_SECRET</code>,
        <code>SKIP_SIGNATURE_CHECK</code>
        (set these in the Render dashboard → Environment).
      </p>
    </section>
    {message_html}
    <section>
      <h2>Threads</h2>
      {thread_rows if thread_rows else "<p>No threads yet.</p>"}
    </section>
    <section>
      <h2>Recent Events</h2>
      <table>
        <thead>
          <tr>
            <th>Received At</th>
            <th>Type</th>
            <th>Thread ID</th>
            <th>Message ID</th>
            <th>Comment ID</th>
            <th>From ID</th>
            <th>Text</th>
            <th>Original</th>
            <th>Edited</th>
            <th>Timestamp</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {event_rows}
        </tbody>
      </table>
    </section>
    <section>
      <h2>Last Webhook Payloads</h2>
      <table>
        <thead>
          <tr>
            <th>Received At</th>
            <th>Payload</th>
          </tr>
        </thead>
        <tbody>
          {payload_rows if payload_rows else "<tr><td colspan='2'>No payloads yet.</td></tr>"}
        </tbody>
      </table>
    </section>
    <section>
      <h2>Last Request Log Summary</h2>
      <table>
        <thead>
          <tr>
            <th>Timestamp</th>
            <th>Method</th>
            <th>Path</th>
            <th>Query</th>
            <th>Status</th>
            <th>Duration (ms)</th>
            <th>Client IP</th>
            <th>Headers</th>
          </tr>
        </thead>
        <tbody>
          {request_rows if request_rows else "<tr><td colspan='8'>No requests logged yet.</td></tr>"}
        </tbody>
      </table>
    </section>
  </body>
</html>
""".strip()


def _render_thread_row(thread: dict[str, Any], draft: str) -> str:
    thread_id = thread["thread_id"]
    last_preview = thread.get("last_preview") or ""
    last_event_type = thread.get("last_event_type") or "unknown"
    return f"""
    <div class="thread-card">
      <div class="thread-meta">
        <strong>Thread:</strong> {thread_id} |
        <strong>Last type:</strong> {last_event_type}
      </div>
      <p><strong>Last message:</strong> {last_preview}</p>
      <form method="post" action="/admin/drafts/{thread_id}">
        <label><strong>Draft</strong></label>
        <textarea name="draft">{draft}</textarea>
        <button type="submit">Save draft</button>
      </form>
      <form method="post" action="/admin/send/{thread_id}">
        <button type="submit">Send draft</button>
      </form>
    </div>
    """


def _render_event_row(event: dict[str, Any]) -> str:
    event_type = event.get("event_type") or ""
    thread_id = event.get("thread_id") or ""
    message_id = event.get("message_id") or ""
    comment_id = event.get("comment_id") or ""
    action_html = ""
    if event_type == "comment" and comment_id:
        action_html = f"""
        <form method="post" action="/admin/reply">
          <input type="hidden" name="comment_id" value="{comment_id}" />
          <input type="text" name="message" placeholder="Reply" required />
          <button type="submit">Reply</button>
        </form>
        """
    elif event_type == "message" and thread_id:
        action_html = f"""
        <form method="post" action="/admin/message-reply">
          <input type="hidden" name="thread_id" value="{thread_id}" />
          <input type="text" name="message" placeholder="DM reply" required />
          <button type="submit">Send DM</button>
        </form>
        """
    return (
        "<tr>"
        f"<td>{event.get('received_at')}</td>"
        f"<td>{event_type}</td>"
        f"<td>{thread_id}</td>"
        f"<td>{message_id}</td>"
        f"<td>{comment_id}</td>"
        f"<td>{event.get('from_id') or ''}</td>"
        f"<td>{event.get('text') or ''}</td>"
        f"<td>{event.get('original_text') or ''}</td>"
        f"<td>{event.get('edited_text') or ''}</td>"
        f"<td>{event.get('timestamp') or ''}</td>"
        f"<td class='actions'>{action_html}</td>"
        "</tr>"
    )


def _render_payload_row(entry: dict[str, Any]) -> str:
    payload = entry.get("payload")
    payload_text = json.dumps(payload, indent=2, ensure_ascii=False)
    return (
        "<tr>"
        f"<td>{entry.get('received_at')}</td>"
        f"<td><pre>{payload_text}</pre></td>"
        "</tr>"
    )


def _render_request_row(entry: dict[str, Any]) -> str:
    headers = entry.get("headers") or {}
    headers_text = json.dumps(headers, ensure_ascii=False)
    return (
        "<tr>"
        f"<td>{entry.get('timestamp')}</td>"
        f"<td>{entry.get('method')}</td>"
        f"<td>{entry.get('path')}</td>"
        f"<td>{entry.get('query')}</td>"
        f"<td>{entry.get('status_code')}</td>"
        f"<td>{entry.get('duration_ms')}</td>"
        f"<td>{entry.get('client_ip')}</td>"
        f"<td><code>{headers_text}</code></td>"
        "</tr>"
    )
