# Talos 4.0

Single-user trading research and execution platform.

## Core flow

- Sources: Telegram, RSS/Atom, REST/JSON, permitted web scraping, webhook, social APIs.
- Archive raw events once in PostgreSQL.
- Telegram recommendations are processed once and stored as normalized signals.
- **Telegram Replay** replays those recommendations from their original publication timestamps. It is historical verification, not a predictive strategy.
- Research papers can be uploaded as PDF, text is extracted, then converted into a structured strategy specification for review.
- Strategies use the generic backtest → optimizer → paper → live pipeline.
- Public market data and charts use CCXT without API keys.
- Private balances and live orders use encrypted broker credentials entered from Admin.
- Long work runs as SQL-backed jobs with checkpoints, automatic retry, stale-job recovery and resume after process restarts.

## Supported exchanges

Binance, OKX, Bybit, Bitget, KuCoin, MEXC (spot through CCXT).

## Render environment

Required:

```text
APP_NAME=Talos
APP_ENV=production
DATABASE_URL=postgresql+psycopg://...
SECRET_KEY=<random long value>
LIVE_TRADING_ENABLED=false
```

Generate a `SECRET_KEY` locally:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

In Render: **Service → Environment → Add Environment Variable → SECRET_KEY → paste the generated value → Save/Deploy**.

The small **Admin** button at the bottom of Talos uses this value only for first-time admin initialization. Broker, Telegram and AI/API credentials can then be stored from the Admin vault. Stored secrets are encrypted before they are written to PostgreSQL and are never returned in plaintext to the browser.

## Telegram

For private/public channels and groups that the dedicated Talos Telegram account can access:

1. Add Telegram as a source.
2. Start `Import / verify history`.
3. Talos scans newest → oldest until Telegram returns an explicit empty page.
4. The job automatically schedules processing of the archived messages.
5. Run **Telegram Replay** to verify the recommendations historically.

Telegram's server-side estimated message count can differ from the number of messages actually accessible to the account. Talos reports the verified accessible count separately.

## Jobs / automation

The web process runs the job loop while it is awake. Jobs persist in PostgreSQL, so closing the page does not cancel work. If a free Render instance sleeps/restarts, the job remains queued/retrying and resumes after the service wakes again.

For a permanent VPS/local Docker deployment, run the dedicated worker:

```bash
python -m app.scripts.job_worker
```

`docker-compose.yml` already includes it.

Automatic retry is used for import, source processing/sync, historical replay, strategy backtests, research extraction and optimization. **Live broker orders are intentionally not blindly retried after an ambiguous broker response**, because that can duplicate real orders; live execution requires idempotent broker/order reconciliation before such a retry is safe.

## Research PDFs

Upload a PDF in Research. Talos extracts text with `pypdf`, stores the paper metadata/text, and runs methodology extraction as a durable job. The resulting specification can be reviewed and saved as a versioned strategy.

## Performance architecture

Python remains the orchestration/integration layer. Numerical bottlenecks should be profiled before moving them to native code. A future C++/pybind11 core is appropriate for millions of repeated simulation/optimization evaluations, but moving Telegram, HTTP, databases and broker integration to C++ would add complexity without useful speedup.

## Build verification

- `/health` → version `4.0.0`
- `/api/public/build` → build id `talos-v4.2.2-render-runtime-fix`


## Telegram notification bot

Talos v4.2 can notify the owner when a new Telegram recommendation is detected, when a watched recommendation is edited, when entry/targets/stop are reached in the live simulation, when a long-running job reaches a terminal error, and with a daily summary.

Setup from the small **Admin** control in the sidebar:

1. Create a bot with `@BotFather` (`/newbot`).
2. Send `/start` to the new bot from the Telegram account that should receive alerts.
3. Admin → Secure Vault → `TELEGRAM_NOTIFY` → save `bot_token`.
4. Admin → Notification bot → **Discover chats** → click your chat. This fills `chat_id`; save `TELEGRAM_NOTIFY` again.
5. Use **Send test**.
6. Choose notification switches and the daily-summary UTC hour.

The dedicated Telegram account used to read source channels remains separate from the notification bot.

### Continuous watcher

The web process runs an embedded watcher by default (`EMBEDDED_LIVE_WATCHER=true`). It syncs enabled sources, processes only changed/new messages, starts a simulation for new parsed Telegram BUY recommendations, checks active simulations, and sends notifications. On a permanent VPS, run `python -m app.scripts.universal_worker` as a separate worker and set `EMBEDDED_LIVE_WATCHER=false` on the web service to avoid duplicate watchers.

Render Free can sleep; when it is asleep it cannot provide true 24/7 monitoring. The next wake/sync catches up messages, while a permanent worker is required for uninterrupted alerts.


## v4.2 live UI

- Minimal helmet intro uses `/frontend/public/talos-helmet.png`.
- On load the intro zooms into the visor/eyes and dissolves into the already-mounted dashboard.
- Overview, markets, paper portfolio, portfolio and live execution status refresh automatically while visible. Existing job/source/live-monitor pages retain their own faster polling loops.
- A light health heartbeat runs while the dashboard is open; durable workers/jobs remain server-side and do not depend on the browser.
