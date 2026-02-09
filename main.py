from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

import db
from admin_routes import router as admin_router
from webhook_routes import router as webhook_router

logger = logging.getLogger("insta-bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

for directory in (TEMPLATES_DIR, STATIC_DIR):
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.exception("directory_create_failed path=%s", directory)

app = FastAPI(title="Instagram Messaging Webhook")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def startup() -> None:
    db.init_db()


@app.middleware("http")
async def log_requests(request: Request, call_next) -> Response:
    start_time = time.perf_counter()
    response_status = 500
    try:
        response = await call_next(request)
        response_status = response.status_code
        return response
    finally:
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            response_status,
            duration_ms,
        )


@app.get("/")
def root() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.head("/health")
def health_head() -> Response:
    return Response(status_code=200)


@app.get("/debug/routes")
def debug_routes() -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for route in app.routes:
        methods = sorted(route.methods or [])
        routes.append({"path": route.path, "methods": methods})
    return routes


app.include_router(webhook_router)
app.include_router(admin_router)
