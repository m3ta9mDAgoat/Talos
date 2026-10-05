# Talos v1.0 — Research-first trading platform

Talos turns heterogeneous data sources into normalized trading decisions that can be researched, optimized, paper-traded and eventually routed to execution adapters without rebuilding the platform.

## What works in v1.0

### Telegram research pipeline
- Dedicated Telegram user account via Telethon / MTProto.
- Public and private channels the dedicated account can access.
- One-click **Import Full History** in resumable 1,000-message batches.
- Auto retry, FloodWait handling and resume from the last stored Telegram message id.
- Raw-message archive in PostgreSQL with original timestamps and edit metadata.
- Hybrid message classifier before parsing:
  - `new_signal`
  - `signal_update`
  - `result_update`
  - `market_commentary`
  - `news`
  - `admin`
  - `chatter`
  - `media_only`
  - `unknown`
- Review queue and manual labels; these labels are retained for later model fine-tuning.
- Generic Arabic / English signal parser with confidence scoring.
- Re-parsing is idempotent and removes stale false-positive signals from older parser versions.

### Profitability analysis
- Public market data through CCXT; no exchange API keys are needed for backtesting.
- Automatic spot-market lookup across Binance, OKX, Bybit, Bitget, KuCoin and MEXC.
- Exchange fallback when the first venue cannot supply candles.
- Configurable historical timeframe, test horizon, fees and slippage.
- Conservative same-candle handling.
- Price-touch stops and higher-timeframe candle-close stops.
- Multiple take-profit targets.
- Equal-weight TP exit model for the first profitability pass.
- Remaining position exits at stop or at the analysis-horizon close.
- Channel metrics:
  - entry-trigger rate
  - TP1/TP2/... hit rate
  - stop rate
  - positive-trade rate
  - average / median trade return
  - profit factor
  - expectancy
  - MFE / MAE
  - model equity return
  - model maximum drawdown
  - missing-market-data count
  - exchange usage
- Results and per-signal backtests are persisted to PostgreSQL.

### Platform foundation
Stable contracts already exist for:
- Data sources
- Feature plugins
- Strategies
- Market-data providers
- Optimizers
- Risk management
- Paper execution
- Future live execution adapters

Telegram is therefore not the platform architecture. A news, RSS, technical, on-chain or ML strategy can plug into the same downstream research and validation path.

## Research safety lifecycle

`DRAFT → BACKTESTED → VALIDATED → PAPER → APPROVED → LIVE`

`LIVE_TRADING_ENABLED=false` remains the default. V1.0 is focused on proving the research path and does **not** auto-enable live capital execution.

## Deploying the update to your existing Render service

1. Replace the existing GitHub repository files with the contents of this Talos folder and commit.
2. Render: **Manual Deploy → Deploy latest commit** (or wait for auto-deploy).
3. Existing Neon data and environment variables stay in place.
4. Startup automatically creates the new `analysis_runs`, `signal_backtests`, and `message_labels` tables. Existing Telegram messages remain untouched.
5. Open Talos → **Sources & Channel Research**.

## Fast path to answer “does this channel make money?”

1. `Test Access`
2. `Import Full History` — one click; wait for `History: complete`.
3. `Analyze Profitability`

`Analyze Profitability` reclassifies and re-parses the stored history automatically, resolves markets, downloads historical candles, runs each eligible signal, and produces the channel report. You do not need to click **Classify + Parse** first.

### Recommended first run
- Timeframe: `15m`
- Horizon: `14 days`
- Fee: `10 bps`
- Slippage: `5 bps`
- Capital/trade: `0.10`
- Require stop: ON
- Exclude edited messages: ON

For greater intrabar precision, rerun the analysis with `5m`. It will take longer and use more exchange API requests.

## Interpreting the result

The first-pass profitability model allocates the trade equally across every stated TP. For example, five targets means 20% of the position is assigned to each. Any unclosed remainder exits at the stated stop, or at the final candle at the configured horizon if no stop happens.

The **Model equity** metric compounds each tested trade using the configured `Capital/trade` fraction. It is a comparison/research curve, not a claim that overlapping real positions would have produced exactly that account balance.

## Historical integrity limitations

Talos flags edited Telegram messages and excludes them by default. It cannot recover a recommendation that the channel owner deleted before Talos began archiving the channel, and old edited posts expose the current snapshot rather than every historical revision. For that reason, newly archived live data has stronger integrity than retrospective channel history.

Historical exchange data can also be unavailable for delisted assets or pairs no longer exposed by a venue's public API. These signals are reported as `no_market_data` rather than being guessed.

## Later: classifier fine-tuning

The v1 classifier is intentionally hybrid and lightweight. The review queue stores your manual labels. Once Talos has a meaningful channel-diverse labeled dataset, a multilingual classifier can be fine-tuned under better conditions and swapped into the classifier contract without changing the parser, backtester, optimizer or execution layers.

## Local development

```bash
cp .env.example .env
docker compose up --build
```

Production uses the root multi-stage `Dockerfile`, so the same application can move from Render to a VPS, Azure, Docker Desktop or a local Linux host without changing application code.
