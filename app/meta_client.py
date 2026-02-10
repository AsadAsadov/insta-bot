from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("insta-bot")

META_PAGE_ACCESS_TOKEN_ENV = "META_PAGE_ACCESS_TOKEN"
META_API_VERSION_ENV = "META_API_VERSION"
GRAPH_BASE_URL = "https://graph.facebook.com"


def _request_graph(
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    access_token = os.getenv(META_PAGE_ACCESS_TOKEN_ENV)
    api_version = os.getenv(META_API_VERSION_ENV, "v24.0")
    if not access_token:
        return {"ok": False, "status_code": None, "error": "META_PAGE_ACCESS_TOKEN not configured", "json": None}

    url = f"{GRAPH_BASE_URL}/{api_version}/{path.lstrip('/')}"
    query = {"access_token": access_token}
    if params:
        query.update(params)

    try:
        response = httpx.post(url, params=query, json=json_payload, timeout=20.0)
        response_json = response.json()
    except httpx.HTTPError as exc:
        logger.exception("meta_api_request_failed path=%s", path)
        return {"ok": False, "status_code": None, "error": str(exc), "json": None}

    if response.is_success:
        return {
            "ok": True,
            "status_code": response.status_code,
            "json": response_json,
            "error": None,
        }

    logger.error("meta_api_request_error path=%s status=%s", path, response.status_code)
    return {
        "ok": False,
        "status_code": response.status_code,
        "json": response_json,
        "error": response_json,
    }


def send_ig_dm(recipient_igsid: str, text: str) -> dict[str, Any]:
    payload = {"recipient": {"id": recipient_igsid}, "message": {"text": text}}
    return _request_graph("me/messages", json_payload=payload)


def send_public_comment_reply(comment_id: str, text: str) -> dict[str, Any]:
    return _request_graph(f"{comment_id}/replies", params={"message": text})


def send_private_comment_reply(comment_id: str, text: str) -> dict[str, Any]:
    payload = {"recipient": {"comment_id": comment_id}, "message": {"text": text}}
    return _request_graph("me/messages", json_payload=payload)


__all__ = [
    "send_ig_dm",
    "send_public_comment_reply",
    "send_private_comment_reply",
]
