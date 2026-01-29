from __future__ import annotations

import os
from typing import Any

import httpx

GRAPH_API_BASE = os.getenv("META_GRAPH_API_BASE", "https://graph.facebook.com/v19.0")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")


def send_dm(ig_user_id: str, text: str) -> dict[str, Any]:
    if not IG_ACCESS_TOKEN:
        raise RuntimeError("IG_ACCESS_TOKEN is not configured")
    url = f"{GRAPH_API_BASE}/{ig_user_id}/messages"
    payload = {"recipient": {"id": ig_user_id}, "message": {"text": text}}
    response = httpx.post(
        url,
        params={"access_token": IG_ACCESS_TOKEN},
        json=payload,
        timeout=20.0,
    )
    response.raise_for_status()
    return response.json()
