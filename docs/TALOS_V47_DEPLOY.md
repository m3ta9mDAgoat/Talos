# Talos v4.7.1 — GitHub + Render deployment

1. Unzip **Talos-v4.7.1-BACKEND-GitHub-Under100.zip** and upload its CONTENTS to the **existing repository root**, preserving `backend/app/...`, root `Dockerfile` and `render.yaml`.
2. Unzip **Talos-v4.7.1-FRONTEND-GitHub-Under100.zip** and upload its CONTENTS to the SAME repository root; do not put either ZIP inside an enclosing folder. GitHub's browser upload limits apply **per batch**, not to the entire existing repository.
3. Render: Docker service; Root Directory **blank**; Dockerfile Path **`./Dockerfile`**; context **`.`**; health path `/health`. Keep your previous encrypted-data `SECRET_KEY` unchanged, and never upload `.env` or broker credentials to GitHub.
4. Required environment: `DATABASE_URL=postgresql+psycopg://...`, `SECRET_KEY`, `EMBEDDED_JOB_WORKER=true`, `EMBEDDED_LIVE_WATCHER=true`, `LIVE_TRADING_ENABLED=false`. PostgreSQL credentials must allow new table creation.
5. `/health` must show `4.7.1`, `database=ready`, `sentiment_replay_worker=true`. Database startup auto-creates `news_sentiment_replay_runs` and `news_sentiment_replay_records`. Open **News Strategy** and run a limited backtest.
6. Default autonomous late-news simulations are replayed from historical publisher time after a ~45 minute maturity buffer; they do **not** generate orders. CSV export shows latest 500 audit decisions. Render Free suspension pauses workers until wake/restart.
7. Test routes, local backtest, database schema and historical market availability on an isolated staging Render/Neon instance before production. No end-to-end broker, cloud, or frontend build guarantee can be made solely from local syntax/unit tests.

**Important:** Success from 1-minute OHLCV after an assumed 5-second publication latency is only a **counterfactual research result**. It does not prove a 5-second fill or true portfolio drawdown. No automatic live-news trading is installed.


## v4.7.1 — Telegram missed-trade alerts

- `Admin -> Notification bot`: enable `Missed winning trades (simulated)` and `Missed losing trades (simulated)`, then save notification settings. Both are enabled by default in the new release.
- Keep encrypted `telegram_notify.bot_token` and `telegram_notify.chat_id` in the Vault (never in GitHub). Use `Send test` to verify delivery.
- Automatic sentiment replays only: if an eligible counterfactual trade was missed and its net simulated P&L is positive or negative, its notification is inserted in a SQL outbox and sent after replay. Manual historical backtests do **not** send per-trade notifications.
- `GET /api/v1/settings/notifications/missed-status` (Admin authenticated) reports pending/sending/sent outcomes, also accessible with `Missed alert delivery status` in Admin.
- An outbox entry is unique per news event and survives backend restart. Failed Telegram sends remain pending with exponential retry. Delivery is at-least-once; a crash after Telegram has accepted a message but before DB acknowledgement may cause a rare duplicate.
- The automatic replay has a ~45-minute maturity buffer for the full historical holding period. These are **not immediate trading signals**. The entry uses a 1m OHLCV opening proxy, not a demonstrated 5-second fill. No broker order results from the new alert feature.
- The release auto-creates one new PostgreSQL table `news_missed_alerts`. Database credentials must allow `CREATE TABLE`. Existing encrypted Vault `SECRET_KEY` must be preserved.
- Validation: local unit tests and static frontend syntax checks passed; remote Telegram, Neon, Render, or full npm build were not performed here.
