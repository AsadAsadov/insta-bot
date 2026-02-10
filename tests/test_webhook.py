import hashlib
import hmac
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

os.environ["META_VERIFY_TOKEN"] = "test-token"
os.environ["DB_PATH"] = "test_app.db"

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402
from app import db  # noqa: E402
import app.webhook_routes as webhook_routes  # noqa: E402


client = TestClient(app)


def setup_function() -> None:
    db.init_db()


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
    assert response.json() == {"ok": True}


def test_invalid_signature_is_ignored(monkeypatch) -> None:
    os.environ["META_APP_SECRET"] = "my-secret"
    payload = {"object": "instagram", "entry": []}
    response = client.post(
        "/webhook",
        json=payload,
        headers={"x-hub-signature-256": "sha256=bad"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "ignored": "invalid_signature"}
    del os.environ["META_APP_SECRET"]


def test_comment_trigger_sends_public_and_private_reply(monkeypatch) -> None:
    db.create_comment_trigger(
        name="Promo",
        trigger_type="contains",
        trigger_value="promo",
        public_reply_text="Check your DM",
        dm_reply_text="Here is your promo code",
        is_active=1,
    )

    called = {"public": 0, "private": 0}

    def fake_public(comment_id: str, text: str):
        called["public"] += 1
        assert comment_id == "cmt_1"
        return {"ok": True, "json": {"id": "reply_1"}}

    def fake_private(comment_id: str, text: str):
        called["private"] += 1
        assert comment_id == "cmt_1"
        return {"ok": True, "json": {"message_id": "m_1"}}

    monkeypatch.setattr(webhook_routes, "send_public_comment_reply", fake_public)
    monkeypatch.setattr(webhook_routes, "send_private_comment_reply", fake_private)

    payload = {
        "object": "instagram",
        "entry": [
            {
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": "cmt_1",
                            "text": "send promo please",
                            "from": {"id": "user_1"},
                        },
                    }
                ]
            }
        ],
    }

    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert called["public"] == 1
    assert called["private"] == 1

    events = [dict(row) for row in db.get_thread_events("user_1")]
    assert any(event["event_type"] == "comment_in" for event in events)
    assert any(event["event_type"] == "comment_public_reply" for event in events)
    assert any(event["event_type"] == "dm_out_private_reply" for event in events)
