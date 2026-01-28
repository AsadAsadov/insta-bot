from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

VERIFY_TOKEN = os.getenv("IG_WEBHOOK_VERIFY_TOKEN", "change-me")

app = FastAPI(title="Insta Bot Web Service")

TEMPLATES = Jinja2Templates(directory="app/templates")

WEBHOOK_EVENTS: List[Dict[str, Any]] = []


@app.get("/")
def healthcheck() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/webhooks/instagram")
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


@app.post("/webhooks/instagram")
async def receive_instagram_webhook(request: Request) -> JSONResponse:
    payload = await request.json()
    WEBHOOK_EVENTS.append(
        {
            "received_at": datetime.utcnow().isoformat() + "Z",
            "payload": payload,
        }
    )
    return JSONResponse(content={"status": "received"})


@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    return TEMPLATES.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "events": list(reversed(WEBHOOK_EVENTS[-50:])),
        },
    )
