# Talos v4.6.0 — Compatibility and verification

## Verified locally
- 36/36 Python backend tests passed (including paper entry/deduplication/stale news checks).
- Python sources compiled with compileall.
- TypeScript/TSX source syntax parsed using locally installed TypeScript.
- 84 statically resolvable API invocations match 108 backend FastAPI route declarations.
- Frontend and backend both report version 4.6.0.
- Backend and frontend archives are separately verified ZIPs of fewer than 100 files.
- Backend archive contains full 97-file runtime backend + root Dockerfile + render.yaml, tests omitted to meet upload limit.

## Requires deployed integration test
- Node dependency install and actual Vite optimized build (`npm install && npm run build`).
- Docker build (Docker not available in local runtime).
- PostgreSQL/Neon and Render deployment, Telegram chat and exchange connections.
- True replay completion under live market source coverage, 8 existing error sources (root causes require production data).
- Real-world data latency, slippage and paper/live reconciliation.

## Safety and intentional feature limits
- News Strategy supports only spot-crypto listing alerts for simulated long buys, not all asset classes.
- News headlines' importance and classification-confidence values are not trade-win probabilities.
- 1-minute OHLCV historical high is not evidence of an attainable execution price.
- Optional auto-paper mode is OFF by default. Autonomous live-money mode is not implemented and cannot be enabled in this new module.
- The pre-existing separate manual/admin Live Execution feature is unchanged.
- Telegram `/paper EVENT_ID` command only authorizes a guarded PAPER trade in the configured private chat.
- News from 45–360 minute feed schedules does not qualify as real-time news trading. Render Free may sleep.

## v4.7.0 sentiment replay changes
- New SQL models `news_sentiment_replay_runs`, `news_sentiment_replay_records` register through `app.models`; the existing database tables are unchanged.
- New API paths: `/api/v1/news-trade/sentiment/catalog`, `/sentiment/runs`, `/sentiment/records`, `/sentiment/metrics`, `/sentiment/export.csv`, and admin-only `POST /api/v1/news-trade/admin/sentiment/backtest`.
- Added background `sentiment_replay_loop` with persisted job cursor and frozen event order (by publication time), automatic windows and manual backtest windows.
- New `SentimentBacktestPanel` inside existing React `News Strategy`, no new frontend runtime dependency.
- Python syntax, baseline 36 tests, new simulator tests, JS/TSX syntax and static frontend↔backend API matcher are locally validated. Actual Render/Neon exchange time series and npm/Docker production build **not verified**.
- **No real-money trade route** was added. Historical OHLCV 1m results do not demonstrate 5s fills. Read `docs/TALOS_V47_SENTIMENT_BACKTEST_AR.md` before interpreting metrics.
