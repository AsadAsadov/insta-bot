from __future__ import annotations

import json
import os
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.state import event_store
from app.webhook import (
    get_instagram_access_token,
    reply_to_comment,
    send_instagram_message,
    set_comment_hidden,
)

security = HTTPBasic(auto_error=False)


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
        raise HTTPException(
            status_code=401,
            detail="Admin credentials not configured.",
            headers={"WWW-Authenticate": "Basic"},
        )
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


@router.get("", response_class=HTMLResponse)
def admin_panel() -> HTMLResponse:
    return HTMLResponse(render_admin_page())


@router.get("/drafts/{thread_id}", response_class=JSONResponse)
def get_draft(thread_id: str) -> JSONResponse:
    draft = event_store.get_draft(thread_id)
    return JSONResponse(content={"thread_id": thread_id, "draft": draft})


@router.post(
    "/drafts/{thread_id}", response_class=JSONResponse, response_model=None
)
async def save_draft(thread_id: str, request: Request) -> JSONResponse:
    payload = await _parse_json_payload(request)
    draft = str(payload.get("draft") or "").strip()
    event_store.set_draft(thread_id, draft)
    return JSONResponse(
        content={"ok": True, "message": f"Draft saved for thread {thread_id}."}
    )


@router.post(
    "/send/{thread_id}", response_class=JSONResponse, response_model=None
)
async def send_draft(thread_id: str, request: Request) -> JSONResponse:
    payload = await _parse_json_payload(request)
    override = str(payload.get("draft") or "").strip()
    if override:
        event_store.set_draft(thread_id, override)
    draft = event_store.get_draft(thread_id)
    if not draft.strip():
        return JSONResponse(
            content={"ok": False, "error": "Draft is empty."}, status_code=200
        )
    access_token = get_instagram_access_token()
    if not access_token:
        return JSONResponse(
            content={
                "ok": False,
                "error": "Access token not configured. Stubbed send.",
            },
            status_code=200,
        )
    try:
        result = send_instagram_message(thread_id, draft)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            content={"ok": False, "error": f"Send failed: {exc}"},
            status_code=200,
        )
    event_store.clear_draft(thread_id)
    return JSONResponse(content={"ok": True, "result": result}, status_code=200)


@router.post("/reply", response_class=JSONResponse, response_model=None)
async def admin_reply(request: Request) -> JSONResponse:
    payload = await _parse_json_payload(request)
    comment_id = str(payload.get("comment_id") or "").strip()
    message = str(payload.get("message") or "").strip()
    access_token = get_instagram_access_token()
    if not comment_id or not message:
        return JSONResponse(
            content={"ok": False, "error": "comment_id and message required"},
            status_code=200,
        )
    if not access_token:
        return JSONResponse(
            content={"ok": False, "error": "Access token not configured."},
            status_code=200,
        )
    try:
        result = reply_to_comment(comment_id, message, access_token)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            content={"ok": False, "error": f"Reply failed: {exc}"},
            status_code=200,
        )
    return JSONResponse(content={"ok": True, "result": result}, status_code=200)


@router.post("/message-reply", response_class=JSONResponse, response_model=None)
async def admin_message_reply(request: Request) -> JSONResponse:
    payload = await _parse_json_payload(request)
    thread_id = str(payload.get("thread_id") or "").strip()
    message = str(payload.get("message") or "").strip()
    access_token = get_instagram_access_token()
    if not thread_id or not message:
        return JSONResponse(
            content={"ok": False, "error": "thread_id and message required"},
            status_code=200,
        )
    if not access_token:
        return JSONResponse(
            content={"ok": False, "error": "Access token not configured."},
            status_code=200,
        )
    try:
        result = send_instagram_message(thread_id, message)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            content={"ok": False, "error": f"DM reply failed: {exc}"},
            status_code=200,
        )
    return JSONResponse(content={"ok": True, "result": result}, status_code=200)


@router.post("/hide", response_class=JSONResponse, response_model=None)
async def admin_hide(request: Request) -> JSONResponse:
    payload = await _parse_json_payload(request)
    comment_id = str(payload.get("comment_id") or "").strip()
    hide = str(payload.get("hide") or "").strip()
    access_token = get_instagram_access_token()
    if not comment_id or hide == "":
        return JSONResponse(
            content={"ok": False, "error": "comment_id and hide required"},
            status_code=200,
        )
    if not access_token:
        return JSONResponse(
            content={"ok": False, "error": "Access token not configured."},
            status_code=200,
        )
    hide_bool = hide.lower() == "true"
    try:
        result = set_comment_hidden(comment_id, hide_bool, access_token)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            content={"ok": False, "error": f"Hide/unhide failed: {exc}"},
            status_code=200,
        )
    return JSONResponse(content={"ok": True, "result": result}, status_code=200)


async def _parse_json_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def render_admin_page(message: str | None = None) -> str:
    verify_token_set = "yes" if os.getenv("META_VERIFY_TOKEN") else "no"
    app_secret_set = "yes" if os.getenv("META_APP_SECRET") else "no"
    skip_signature = os.getenv("SKIP_SIGNATURE_CHECK")
    admin_credentials = _get_admin_credentials()
    admin_warning = ""
    if not admin_credentials:
        admin_warning = (
            "<p><strong>ADMIN_USER/ADMIN_PASS not set.</strong> "
            "Admin routes are disabled until these env vars are configured.</p>"
        )
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
      input, select, textarea {{ margin-right: 0.5rem; }}
      textarea {{ width: 100%; min-height: 70px; }}
      .thread-card {{ border: 1px solid #ddd; padding: 1rem; margin-bottom: 1rem; }}
      .thread-meta {{ font-size: 0.9rem; color: #555; }}
      .actions .action-row {{ display: flex; gap: 0.5rem; margin-bottom: 0.5rem; }}
      .actions input {{ flex: 1; }}
    </style>
    <script>
      async function postJson(url, payload) {{
        const response = await fetch(url, {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(payload || {{}}),
        }});
        let data = {{}};
        try {{
          data = await response.json();
        }} catch (err) {{
          data = {{}};
        }}
        return {{ ok: response.ok, data }};
      }}

      async function saveDraft(threadId) {{
        const draftEl = document.getElementById(`draft-${{threadId}}`);
        const draft = draftEl ? draftEl.value : "";
        const result = await postJson(`/admin/drafts/${{threadId}}`, {{ draft }});
        alert(result.data.message || result.data.error || "Draft saved.");
      }}

      async function sendDraft(threadId) {{
        const draftEl = document.getElementById(`draft-${{threadId}}`);
        const draft = draftEl ? draftEl.value : "";
        const result = await postJson(`/admin/send/${{threadId}}`, {{ draft }});
        alert(result.data.result ? "Draft sent." : result.data.error || "Send complete.");
        if (result.data.result) {{
          window.location.reload();
        }}
      }}

      async function replyToComment(commentId, inputId) {{
        const messageEl = document.getElementById(inputId);
        const message = messageEl ? messageEl.value : "";
        const result = await postJson("/admin/reply", {{ comment_id: commentId, message }});
        alert(result.data.result ? "Reply sent." : result.data.error || "Reply complete.");
      }}

      async function replyToThread(threadId, inputId) {{
        const messageEl = document.getElementById(inputId);
        const message = messageEl ? messageEl.value : "";
        const result = await postJson("/admin/message-reply", {{ thread_id: threadId, message }});
        alert(result.data.result ? "DM sent." : result.data.error || "Send complete.");
      }}
    </script>
  </head>
  <body>
    <h1>Webhook Admin</h1>
    <section>
      <p>META_VERIFY_TOKEN set: <strong>{verify_token_set}</strong></p>
      <p>META_APP_SECRET set: <strong>{app_secret_set}</strong></p>
      <p>SKIP_SIGNATURE_CHECK: <strong>{skip_signature or "not set"}</strong></p>
      {admin_warning}
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
      <label><strong>Draft</strong></label>
      <textarea id="draft-{thread_id}">{draft}</textarea>
      <button type="button" onclick="saveDraft('{thread_id}')">Save draft</button>
      <button type="button" onclick="sendDraft('{thread_id}')">Send draft</button>
    </div>
    """


def _render_event_row(event: dict[str, Any]) -> str:
    event_type = event.get("event_type") or ""
    thread_id = event.get("thread_id") or ""
    message_id = event.get("message_id") or ""
    comment_id = event.get("comment_id") or ""
    action_html = ""
    if event_type == "comment" and comment_id:
        input_id = f"comment-reply-{comment_id}"
        action_html = f"""
        <div class="action-row">
          <input type="text" id="{input_id}" placeholder="Reply" />
          <button type="button" onclick="replyToComment('{comment_id}', '{input_id}')">Reply</button>
        </div>
        """
    elif event_type == "message" and thread_id:
        input_id = f"thread-reply-{thread_id}"
        action_html = f"""
        <div class="action-row">
          <input type="text" id="{input_id}" placeholder="DM reply" />
          <button type="button" onclick="replyToThread('{thread_id}', '{input_id}')">Send DM</button>
        </div>
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
