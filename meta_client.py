from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("insta-bot")

META_PAGE_ACCESS_TOKEN_ENV = "META_PAGE_ACCESS_TOKEN"
META_API_VERSION_ENV = "META_API_VERSION"


def send_ig_dm(recipient_igsid: str, text: str) -> dict[str, Any]:
    access_token = os.getenv(META_PAGE_ACCESS_TOKEN_ENV)
    api_version = os.getenv(META_API_VERSION_ENV, "v24.0")
    if not access_token:
        return {
            "ok": False,
            "error": "META_PAGE_ACCESS_TOKEN not configured",
            "status_code": None,
            "json": None,
        }
    url = f"https://graph.facebook.com/{api_version}/me/messages"
    payload = {"recipient": {"id": recipient_igsid}, "message": {"text": text}}
    try:
        response = httpx.post(
            url,
            params={"access_token": access_token},
            json=payload,
            timeout=20.0,
        )
        response_json = response.json()
    except httpx.HTTPError as exc:
        logger.exception("send_dm_fail recipient=%s error=%s", recipient_igsid, exc)
        return {
            "ok": False,
            "status_code": None,
            "error": str(exc),
            "json": None,
        }
    if response.is_success:
        logger.info("send_dm_success recipient=%s", recipient_igsid)
        return {
            "ok": True,
            "status_code": response.status_code,
            "json": response_json,
            "error": None,
        }
    logger.error(
        "send_dm_fail recipient=%s status=%s", recipient_igsid, response.status_code
    )
    return {
        "ok": False,
        "status_code": response.status_code,
        "error": response_json,
        "json": response_json,
    }


__all__ = ["send_ig_dm"]
