from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import FastAPI, Request, Response

from app.admin import router as admin_router
from app.db import init_db
from app.state import event_store
from app.webhook import router as webhook_router

logger = logging.getLogger("insta-bot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="Instagram Messaging Webhook")


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.middleware("http")
async def log_requests(request: Request, call_next) -> Response:
    start_time = time.perf_counter()
    client_ip = request.client.host if request.client else "unknown"
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip() or client_ip
    header_subset = {
        "user-agent": request.headers.get("user-agent"),
        "content-type": request.headers.get("content-type"),
        "x-hub-signature-256": request.headers.get("x-hub-signature-256"),
        "x-hub-signature": request.headers.get("x-hub-signature"),
    }
    response_status = 500
    try:
        response = await call_next(request)
        response_status = response.status_code
        return response
    finally:
        duration_ms = (time.perf_counter() - start_time) * 1000
        query_string = request.url.query
        event_store.add_request_log(
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "method": request.method,
                "path": request.url.path,
                "query": query_string,
                "client_ip": client_ip,
                "headers": header_subset,
                "status_code": response_status,
                "duration_ms": round(duration_ms, 2),
            }
        )
        logger.info(
            "request method=%s path=%s query=%s client_ip=%s status=%s duration_ms=%.2f headers=%s",
            request.method,
            request.url.path,
            query_string,
            client_ip,
            response_status,
            duration_ms,
            header_subset,
        )


@app.get("/")
def root() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.head("/health")
def health_head() -> Response:
    return Response(status_code=200)


@app.get("/debug/health")
def debug_health() -> dict[str, bool | str]:
    return {"status": "ok", "webhook_get": True, "webhook_post": True}


@app.get("/debug/routes")
def debug_routes() -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for route in app.routes:
        methods = sorted(route.methods or [])
        routes.append({"path": route.path, "methods": methods})
    return routes


app.include_router(webhook_router)
app.include_router(admin_router)
