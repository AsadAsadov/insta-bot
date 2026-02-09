from __future__ import annotations

import os
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.state import event_buffer
from app.webhook import get_access_token, reply_to_comment, set_comment_hidden

router = APIRouter(prefix="/admin", tags=["admin"])
security = HTTPBasic()


def require_admin(
    credentials: HTTPBasicCredentials = Depends(security),
) -> None:
    admin_user = os.getenv("ADMIN_USER")
    admin_pass = os.getenv("ADMIN_PASS")
    if not admin_user or not admin_pass:
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


@router.get("", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin_panel(request: Request) -> HTMLResponse:
    return HTMLResponse(render_admin_page())


@router.post(
    "/reply", response_class=HTMLResponse, dependencies=[Depends(require_admin)]
)
def admin_reply(
    request: Request, comment_id: str = Form(...), message: str = Form(...)
) -> HTMLResponse:
    access_token = get_access_token()
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
    "/hide", response_class=HTMLResponse, dependencies=[Depends(require_admin)]
)
def admin_hide(
    request: Request, comment_id: str = Form(...), hide: str = Form(...)
) -> HTMLResponse:
    access_token = get_access_token()
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
    verify_token_set = "yes" if os.getenv("VERIFY_TOKEN") else "no"
    events = event_buffer.recent(20)
    event_rows = "\n".join(
        f"<tr><td>{event.get('received_at')}</td>"
        f"<td>{event.get('comment_id')}</td>"
        f"<td>{event.get('media_id')}</td>"
        f"<td>{event.get('from_id')}</td>"
        f"<td>{event.get('text')}</td>"
        f"<td>{event.get('timestamp')}</td></tr>"
        for event in events
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
      input, select {{ margin-right: 0.5rem; }}
    </style>
  </head>
  <body>
    <h1>Webhook Admin</h1>
    <p>VERIFY_TOKEN set: <strong>{verify_token_set}</strong></p>
    {message_html}
    <section>
      <h2>Reply to Comment</h2>
      <form method="post" action="/admin/reply">
        <input type="text" name="comment_id" placeholder="Comment ID" required />
        <input type="text" name="message" placeholder="Message" required />
        <button type="submit">Reply</button>
      </form>
    </section>
    <section>
      <h2>Hide/Unhide Comment</h2>
      <form method="post" action="/admin/hide">
        <input type="text" name="comment_id" placeholder="Comment ID" required />
        <select name="hide">
          <option value="true">Hide</option>
          <option value="false">Unhide</option>
        </select>
        <button type="submit">Submit</button>
      </form>
    </section>
    <section>
      <h2>Last 20 Comment Events</h2>
      <table>
        <thead>
          <tr>
            <th>Received At</th>
            <th>Comment ID</th>
            <th>Media ID</th>
            <th>From ID</th>
            <th>Text</th>
            <th>Timestamp</th>
          </tr>
        </thead>
        <tbody>
          {event_rows}
        </tbody>
      </table>
    </section>
  </body>
</html>
""".strip()
