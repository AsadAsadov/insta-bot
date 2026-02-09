from __future__ import annotations

import os
from typing import Any

import httpx

GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v20.0")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
IG_USER_ID = os.getenv("IG_USER_ID")


def send_dm(recipient_id: str, text: str) -> dict[str, Any]:
    if not ACCESS_TOKEN:
        raise RuntimeError("ACCESS_TOKEN is not configured")
    if not IG_USER_ID:
        raise RuntimeError("IG_USER_ID is not configured")
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{IG_USER_ID}/messages"
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    response = httpx.post(
        url,
        params={"access_token": ACCESS_TOKEN},
        json=payload,
        timeout=20.0,
    )
    response.raise_for_status()
    return response.json()


def send_comment_reply(comment_id: str, text: str) -> dict[str, Any]:
    if not ACCESS_TOKEN:
        raise RuntimeError("ACCESS_TOKEN is not configured")
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{comment_id}/replies"
    payload = {"message": text}
    response = httpx.post(
        url,
        params={"access_token": ACCESS_TOKEN},
        json=payload,
        timeout=20.0,
    )
    response.raise_for_status()
    return response.json()
