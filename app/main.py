from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

from app.admin import router as admin_router
from app.db import init_db
from app.webhook import router as webhook_router

logger = logging.getLogger("insta-bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="Instagram Messaging Webhook")


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def root() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/debug/routes")
def debug_routes() -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for route in app.routes:
        methods = sorted(route.methods or [])
        routes.append({"path": route.path, "methods": methods})
    return routes


app.include_router(webhook_router)
app.include_router(admin_router)
