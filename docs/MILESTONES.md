# Talos Milestones

## v0.2 - Current vertical slice
- Portable FastAPI + React structure.
- PostgreSQL-ready source/raw-event/signal models.
- Generic Telegram source definition.
- Telethon historical/live connector implementation.
- Render-safe Telethon `StringSession` workflow.
- Deterministic Arabic/English crypto-signal parser.
- Normalized signal schema.
- Single-signal event-driven backtester with entry zones, targets, price-touch stops, candle-close stops, MFE and MAE.
- Source CRUD and parser/backtest APIs.
- Basic dashboard, Sources view and Signal Lab.
- Six backend tests passing.
- One-image production Docker deployment so Render is not part of business logic.

## v0.3 - Next
- Persistent signal extraction from archived raw Telegram events.
- Channel-specific parser profiles and review queue.
- CCXT market discovery and historical OHLCV provider.
- Parquet cache and data-quality metadata.
- Historical Telegram import job/worker instead of request-bound import.
- First end-to-end channel backtest.

## Later
- Strategy configuration/versioning.
- Portfolio backtester and fee/slippage models.
- Optimizer plugins + walk-forward + Monte Carlo.
- Paper execution.
- Risk/portfolio gates.
- Auto exchange resolver.
- Live spot execution.
- News/RSS/custom source plugins and non-Telegram strategies.
- MT5 agent and additional asset classes.
