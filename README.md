# insta-bot

A CustomTkinter desktop application that helps collect Instagram commenters, send DMs, and post comments to feeds or hashtag searches using Playwright.

## Features
- Extract commenters from a post or reel.
- Maintain a sent-users list and blacklist.
- Send DMs with configurable delays and breaks.
- Post comments on feed posts or hashtag search results.

## Requirements
- Python 3.10+
- Playwright browsers installed locally

## Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install
```

## Run
```bash
insta-bot
```

Alternatively:
```bash
python -m insta_bot.app
```

## Data Files
Runtime data is stored in the `data/` directory:
- `sent_users.txt`
- `blacklist.txt`
- `users_saved.txt`
- `dm_progress.json`
- `commented_posts.txt`

These files are created automatically when the app runs.
