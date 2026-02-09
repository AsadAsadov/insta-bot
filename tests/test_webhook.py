import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

os.environ["META_VERIFY_TOKEN"] = "test-token"
os.environ["META_APP_SECRET"] = "test-secret"
os.environ["REQUIRE_SIGNATURE"] = "false"

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402


client = TestClient(app)


def test_webhook_verify_returns_plain_text() -> None:
    response = client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "test-token",
            "hub.challenge": "challenge-text",
        },
    )
    assert response.status_code == 200
    assert response.text == "challenge-text"


def test_webhook_head_returns_ok() -> None:
    response = client.head("/webhook")
    assert response.status_code == 200


def test_webhook_post_acknowledges() -> None:
    payload = {
        "object": "instagram",
        "entry": [
            {
                "messaging": [
                    {
                        "sender": {"id": "111"},
                        "recipient": {"id": "222"},
                        "timestamp": 1710000000,
                        "message": {"text": "Salam"},
                    }
                ]
            }
        ],
    }
    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "received"
