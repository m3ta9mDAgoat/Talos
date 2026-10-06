# Talos v2.1.1 (fixed deployment package)

**Build marker:** `talos-v2.1.1-fixed`

# Talos v2.1

Talos is a modular trading research and execution platform. Telegram is one source, not the architecture.

## What is real in v2.1

- Persistent Telegram archive in PostgreSQL/Neon.
- Full-history importer that is resumable and marks completion only after an explicit empty Telegram page (`verified_empty_tail`), not because a batch happened to be short.
- Incremental processing state per raw message using content hash + classifier/parser versions. Historical messages are classified/parsed once. New or edited messages only are processed on subsequent runs.
- Persisted normalized signals with pagination/counts.
- Profitability backtests read SQL signals; they do **not** re-classify/re-parse the archive.
- Public market-data / ticker routing through CCXT for Binance, OKX, Bybit, Bitget, KuCoin and MEXC.
- Venue comparison by spread/liquidity proxy.
- Persistent paper account, orders and positions with live public prices.
- Real live-order adapter through CCXT, hard-locked unless `LIVE_TRADING_ENABLED=true` **and** the request explicitly confirms a real order.
- Exchange private connectivity checks from the encrypted Talos Secure Vault (environment variables remain supported as fallback).
- Backtest optimizer for portfolio risk fraction over a completed analysis run without redownloading historical prices.
- Persistent execution audit log.
- Versioned strategy registry and plugin contracts for Telegram, news, technical, ML or custom strategies.
- Always-on Telegram live worker for VPS/local deployment (`python -m app.scripts.live_worker`).
- Docker deployment remains portable across Render, VPS, Azure, local Linux/Windows Docker.

## Important hosting fact

Render Free can sleep. It is suitable for the dashboard/API and manual sync/research, but it cannot guarantee a 24/7 Telegram listener or real-time execution. For continuous live processing, move the same Dockerized project to an always-on VPS/local server and run the API plus `app.scripts.live_worker`.

## Upgrade from v1.x

Do not delete Neon. Deploy v2.1 against the same `DATABASE_URL`; new tables are created automatically. Existing raw messages, signals and analyses remain.

After deployment:

1. Sources → **Import / Verify Full History**. v2.1 performs a fresh newest→oldest verification pass and deduplicates against the existing archive, so it can fill gaps left by the old cursor logic. Completion requires an explicit empty Telegram page.
2. Sources → **Process Unprocessed**. This creates processing-state rows and reuses them forever unless a message changes or parser/classifier versions change.
3. Signals → verify the exact persisted signal count and browse with pagination.
4. Backtests → run the channel analysis. It reads persisted SQL signals only.
5. Optimizer → enter a completed analysis run ID to optimize portfolio risk fraction.
6. Exchanges → verify public connectivity. Add exchange secrets only when needed.
7. Paper Trading → use live public prices with simulated cash/orders/positions.
8. Live Trading remains locked until you deliberately enable it.

## Secure Vault and admin login

Talos v2.1 adds a single-user admin login. On first deployment, the UI asks once for the existing Render `SECRET_KEY` as the setup key and lets you choose an admin password. After that, the dashboard requires login.

Settings → Secure Vault stores Telegram and exchange credentials encrypted at rest in PostgreSQL. Secret values are never returned to the browser after saving. Do not rotate `SECRET_KEY` without first replacing/re-entering vault secrets, because it is also the encryption root.

Environment variables for broker credentials still work as a fallback, but are no longer required after vault setup. Keep withdrawal permissions disabled.

## Live safety

`LIVE_TRADING_ENABLED=false` is the default. Talos never needs withdrawal permission. Use API keys limited to read + spot trading, and IP-whitelist them when the exchange supports it.

## Fine-tuning later

Do not fine-tune the message classifier before collecting enough corrected labels across multiple channels. The current rules/context classifier plus manual review creates the training dataset. Fine-tuning becomes worthwhile only after the dataset is large and diverse enough to measure an out-of-sample improvement.
