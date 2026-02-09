# insta-bot

Production-ready FastAPI webhook server for the Meta Instagram Messaging API.

## Features
- `GET /webhook` for Meta verification (hub challenge).
- `POST /webhook` for Instagram messaging events with signature validation.
- Stores inbound DM messages in SQLite (`messages` table).
- Auto-replies to each DM with a configurable greeting.
- `GET /health` for load balancer health checks.

## Requirements
- Python 3.10+

## Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run locally
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Environment variables
```bash
export VERIFY_TOKEN="your-webhook-verify-token"
export META_ACCESS_TOKEN="your-generated-graph-api-token"
export APP_SECRET="your-app-secret"
export IG_BUSINESS_ACCOUNT_ID="your-ig-business-account-id"
export GRAPH_API_VERSION="v20.0"  # optional, defaults to v20.0
```

Optional:
```bash
export DATABASE_URL="sqlite:///data/insta_bot.db"
```

## Webhook configuration
1. Deploy the service and ensure it is publicly reachable.
2. Set your Meta webhook callback URL to:
   ```
   https://<your-domain>/webhook
   ```
3. Use the same `VERIFY_TOKEN` value in the Meta webhook configuration.
4. Subscribe the app to the **Instagram** messaging webhook events, including `messages`.
5. Ensure your app has the required permissions for Instagram Messaging API and that your
   Instagram Business account is connected to a Facebook Page.

## Signature verification
`POST /webhook` validates the `X-Hub-Signature-256` header with `APP_SECRET`. Requests with
missing or invalid signatures are rejected with `403`.

## Render deployment
This repository includes a `render.yaml`. You can also deploy using a `Procfile` entry:
```
web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```
