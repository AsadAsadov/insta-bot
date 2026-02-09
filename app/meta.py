from __future__ import annotations

import os
from typing import Any

import httpx

GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v20.0")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
IG_BUSINESS_ACCOUNT_ID = os.getenv("IG_BUSINESS_ACCOUNT_ID")


def send_dm(recipient_id: str, text: str) -> dict[str, Any]:
    if not META_ACCESS_TOKEN:
        raise RuntimeError("META_ACCESS_TOKEN is not configured")
    if not IG_BUSINESS_ACCOUNT_ID:
        raise RuntimeError("IG_BUSINESS_ACCOUNT_ID is not configured")
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{IG_BUSINESS_ACCOUNT_ID}/messages"
    payload = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    response = httpx.post(
        url,
        params={"access_token": META_ACCESS_TOKEN},
        json=payload,
        timeout=20.0,
    )
    response.raise_for_status()
    return response.json()
