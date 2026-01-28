# insta-bot

A server-based Instagram automation service built with FastAPI. It exposes Instagram webhook endpoints and a simple admin panel for viewing received events.

## Features
- Instagram Webhooks (GET verification + POST events)
- Admin panel at `/admin`
- Render-compatible structure (Uvicorn entrypoint at `app/main.py`)

## Requirements
- Python 3.10+

## Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run (local)
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Environment
Set the webhook verification token used by Instagram during the GET verification step:

```bash
export IG_WEBHOOK_VERIFY_TOKEN=your-verify-token
```

## Endpoints
- `GET /` — health check
- `GET /webhooks/instagram` — Instagram webhook verification (expects `hub.mode`, `hub.verify_token`, `hub.challenge`)
- `POST /webhooks/instagram` — receives webhook events (JSON)
- `GET /admin` — admin panel showing recent webhook events
