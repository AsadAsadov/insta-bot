from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("insta-bot")
GRAPH_BASE = "https://graph.facebook.com"


def _post(path: str, payload: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    token = os.getenv("META_PAGE_ACCESS_TOKEN", "").strip()
    api_version = os.getenv("META_API_VERSION", "v24.0").strip() or "v24.0"
    if not token:
        logger.warning("send_dm_fail status_code=%s response=%s", None, {"error": "token_missing"})
        return {"ok": False, "status_code": None, "json": {"error": "META_PAGE_ACCESS_TOKEN missing"}, "error": "token_missing"}

    url = f"{GRAPH_BASE}/{api_version}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = httpx.post(url, json=payload, params=params, headers=headers, timeout=20.0)
        try:
            body: Any = response.json()
        except ValueError:
            body = {"raw": response.text[:500]}
        if response.is_success:
            logger.info("send_dm_success status_code=%s response=%s", response.status_code, body)
            return {"ok": True, "status_code": response.status_code, "json": body, "error": None}
        logger.warning("send_dm_fail status_code=%s response=%s", response.status_code, body)
        return {"ok": False, "status_code": response.status_code, "json": body, "error": body}
    except httpx.HTTPError as exc:
        logger.warning("send_dm_fail status_code=%s response=%s", None, {"error": str(exc)})
        return {"ok": False, "status_code": None, "json": None, "error": str(exc)}


def send_ig_dm(recipient_igsid: str, text: str) -> dict[str, Any]:
    use_fallback = os.getenv("META_DM_USE_IGID_ENDPOINT", "0") == "1"
    if use_fallback:
        ig_business_id = os.getenv("META_IG_BUSINESS_ID", "").strip()
        if not ig_business_id:
            return {"ok": False, "status_code": None, "json": {"error": "META_IG_BUSINESS_ID missing while fallback enabled"}, "error": "ig_business_id_missing"}
        return _post(f"{ig_business_id}/messages", payload={"recipient": {"id": recipient_igsid}, "message": {"text": text}})
    return _post("me/messages", payload={"recipient": {"id": recipient_igsid}, "message": {"text": text}})


def send_public_comment_reply(comment_id: str, text: str) -> dict[str, Any]:
    return _post(f"{comment_id}/replies", params={"message": text})


def send_private_comment_reply(comment_id: str, text: str) -> dict[str, Any]:
    return _post("me/messages", payload={"recipient": {"comment_id": comment_id}, "message": {"text": text}})
