# Talos v3.0 — Universal Trading Research & Execution Platform

Talos is **not a Telegram bot**. Telegram is one data-source adapter inside a reusable research and execution platform.

## Core workflow

`Research idea → data source(s) → no-code strategy → durable backtest → optimization → paper deployment → live deployment`

### Data sources available from the UI

- Telegram channels/groups (history + incremental sync when the dedicated account can read them)
- RSS / Atom feeds
- Generic HTTP JSON APIs
- Generic web-page scraper using CSS selectors for sites where automated access is permitted
- Webhooks for arbitrary external systems
- X/social APIs through the generic authenticated JSON adapter
- Public exchange market data through CCXT

Source credentials/headers can be stored encrypted. Secrets are never returned to the browser after storage.

### Research Studio

- Search scholarly metadata with Crossref.
- Select a paper and/or paste its method/notes.
- Generate an editable Talos strategy draft.
- If an OpenAI-compatible AI endpoint is configured in **Settings → AI**, Talos asks it for a strict strategy JSON draft.
- Without AI, Talos provides a conservative heuristic draft. **Generated research logic must be reviewed before backtesting.**

### No-code Strategy Studio

Three working strategy families are included:

1. **Technical rules** — visual conditions using close, SMA, EMA, RSI, return %, volume ratio and SMA-distance features.
2. **Parsed signal source** — consumes persisted normalized signals, e.g. Telegram recommendations.
3. **Source event** — trades from news/RSS/API/webhook events using keyword filters and mapped symbols.

Each strategy stores its definition and optimization space in PostgreSQL. Backtests, optimizers and deployments use the same stored strategy definition.

> "Any idea without code" has a real boundary: Talos can compose the blocks it knows. A genuinely new mathematical operator, proprietary protocol, JS-only/private site, or new asset class still needs a new plugin block. The platform is designed so that adding that plugin does **not** require rebuilding the rest of Talos.

### Backtesting

- Uses public CCXT market data; private broker keys are not required.
- Durable SQL-backed runs: results persist and runs can be resumed.
- Fees and slippage are explicit.
- Source-signal strategies use entry/TP/SL rules from normalized signals.
- Technical strategies use OHLCV features and deterministic simulation.
- Event strategies align archived events with future market candles.
- Metrics include trade returns, win/positive rate, profit factor, model equity return, max drawdown and per-symbol breakdown.

### Optimizer

Optimization is also durable. A declared parameter grid is evaluated against the same backtest engine; the run stores every trial and the best return/drawdown score. Current UI exposes TP/SL ranges and the backend DSL supports condition parameter paths as well.

### Paper and live execution

- Persistent paper cash, orders and positions.
- Strategy deployments can be created in **paper** mode and manually ticked on Render, or continuously evaluated by the worker on a persistent server.
- Live deployments require:
  1. connected encrypted exchange credentials,
  2. global live-trading switch,
  3. deployment-level live confirmation,
  4. explicit execution confirmation for manual ticks.
- Supported CCXT spot venues: Binance, OKX, Bybit, Bitget, KuCoin and MEXC.
- Exchange withdrawal permission is **never required** and should remain disabled.

## Hosting

The application is portable. The same Docker project can run on Render, a VPS, Azure, a local Linux/Windows Docker host, etc.

Render Free is useful for UI/API research work but sleeps when idle. Continuous source monitoring or automatic live execution requires a persistent worker. `docker-compose.yml` includes a `worker` service for that purpose.

## Deploy on the existing Render setup

Keep the existing `DATABASE_URL`, `SECRET_KEY` and other Environment Variables. Upload/commit this repository at the repo root, then deploy with a cleared build cache.

Verify:

- `/health` → version `3.0.0`
- `/api/public/build` → build id `talos-v3-universal-strategy-studio`
- Sidebar → `TALOS v3.0`

Existing v2 tables are retained. v3 creates new strategy/research/deployment tables without deleting the archived Telegram data.

## Local persistent deployment

```bash
docker compose up -d --build
```

This starts backend, frontend, PostgreSQL, Redis and the continuous universal worker.

## Tests

Backend deterministic unit tests: `16 passed` in the packaged build.
