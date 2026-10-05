# Talos

Talos is a modular quantitative research and automated trading platform. Telegram is only the first source adapter; the platform is designed so news, RSS, technical, ML, on-chain, or custom strategies reuse the same backtest, validation, risk, portfolio and execution layers.

## Core flow

`Data Sources -> Raw Archive -> Normalization/Features -> Strategy -> Standard Decision -> Backtest -> Optimization -> Validation -> Paper -> Risk -> Exchange Router -> Execution`

## Safety boundary

Talos starts with `LIVE_TRADING_ENABLED=false` and uses the lifecycle:

`DRAFT -> BACKTESTED -> VALIDATED -> PAPER -> APPROVED -> LIVE`

A strategy does not choose capital or bypass the risk engine.

## Current v0.3 vertical slice

Implemented now:
- FastAPI backend and React/TypeScript dashboard shell.
- Source/raw-event/signal database models.
- Telegram source connector using a dedicated account through Telethon.
- Telegram access test, incremental historical import, on-demand latest-message sync and message browser.
- Stored-message parsing into persisted normalized signals with edit metadata and idempotent upserts.
- Generic Arabic/English spot-signal parser with confidence/review behavior.
- Standard entry/target/stop signal schema.
- Single-signal backtest supporting entry zones, TP hits, price stops, higher-timeframe candle-close stops, MFE and MAE.
- Sources UI with Test Access, Import History, Sync New Messages, View Messages and Parse Signals actions.
- Basic Signal Lab UI.
- One portable production Docker image for Render/VPS/local migration.

See `docs/MILESTONES.md` for the build order.

## Local backend test

```bash
cd backend
pytest -q
```

## Local Docker development

1. Copy `.env.example` to `.env`.
2. Keep all secrets outside Git.
3. Run:

```bash
docker compose up --build
```

Backend health: `http://localhost:8000/health`
Frontend: `http://localhost:3000`

## Telegram authorization

Use a separate Telegram account. After creating an app at `my.telegram.org`, set `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` locally and run:

```bash
cd backend
python -m app.scripts.telegram_session
```

The script outputs a Telethon `StringSession`. Treat it like a password and put it directly into the hosting provider as `TELEGRAM_SESSION_STRING`; never commit it.

## Production / Render

The root `Dockerfile` builds the React app and serves it from FastAPI as one portable container. `render.yaml` contains no Talos business logic; moving later to a VPS or local Docker host does not require rewriting the application.

For Render free hosting, use external PostgreSQL/Redis URLs rather than relying on local ephemeral disk for persistent state.


## Telegram source workflow (v0.3)

For every configured Telegram source:

1. **Test Access** verifies that the dedicated Talos account can resolve and read the chat.
2. **Import History** imports the next chronological batch (1,000 by default) and advances a stored cursor. Repeat until the UI reports history complete.
3. **Sync New Messages** fetches the latest batch and deduplicates it. This is intentionally on-demand on Render Free because the service can sleep.
4. **View Messages** shows the latest raw archived messages from PostgreSQL.
5. **Parse Signals** runs the deterministic Telegram parser over the latest revision of each stored message and upserts normalized signals. Edited messages are tagged in signal metadata.

Private channels work when the dedicated Talos Telegram account is already a member. Public usernames, `t.me` links, private invite links (when already joined), and numeric peer IDs are accepted.
