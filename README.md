# Talos v4.7.6 — adaptive Telegram signal horizon

Live paper signal simulations now select a per-trade review time from target distance and past 1m volatility. Duration, decision inputs, planned exit, and outcome are persisted on the original simulation record. Telegram completion notifications use a retryable database outbox. This is research-only and not live trading.

See `docs/TALOS_V475_ADAPTIVE_HORIZON_AR.md` for the Arabic strategy and limitations. v4.7.6 additionally recovers the last pre-deadline market candle after worker restarts/advanced cursors without looking into future bars.

# Talos v4.7.3 — Telegram Replay archive-first recovery

**This version replaces the v4.7.2 replay-empty workaround.** It reads actual archived Telegram messages if the older classified signal table contains zero eligible BUY signals. It supports `#SOLUSDT`, `BTCUSDT`, `BTC/USDT`, `Entry`, `TP/Target`, `SL/Stop` variants, and revised content. It does not invent missing prices, entries, or targets.

- Default replay: **Edited latest at ORIGINAL time** (`optimistic`). This intentionally uses information from later edits at earlier timestamps; results have **look-ahead bias** and are **research-only**. Use `from_edit` for time-honest edited-message replay.
- A legacy zero-result replay remains in history. Press Start after deploying this version to create a new job; the replay fingerprint contains the engine version to prevent reusing old `NO DATA` jobs.
- Replay can run directly on the archive without needing a successful classification job. Source readiness shows recovery count, rejection counts, and small excerpts of rejected messages. If no signals match, inspect these excerpts to add a channel-specific parser rather than fabricate results.
- Removed automatic expensive readiness reload on every job progress poll, a likely cause of 502 and heavy pressure on Render/Neon.
- Historical candles still depend on available public exchange data. If the token is delisted or its old candles are unavailable, the replay will display `no_market_data`; no simulated return will be invented.
- **Do not use edited-at-original-time results to justify live automated trading.**

Upload the contents of the BACKEND ZIP, then the contents of the FRONTEND ZIP into the root of the same existing GitHub repository. Keep Render Root Directory blank and Dockerfile Path `./Dockerfile` (existing v4.7 settings).

## Local verification

SQLite integration with archived original + edited messages: 2 recovered candidates, deterministic timestamps, 2 persisted mock trade results, unrecognized-message rejection, and duplicate job dedupe passed. Python syntax and TypeScript/TSX parsing passed. Exchange candle retrieval, real Render/Neon, Telegram source history, and full frontend dependency build were not tested in this sandbox.

---

## v4.7.2 Telegram Replay and live edit evidence

Telegram Replay now diagnoses empty successful-looking results; edit handling explicitly offers original-only, **include edited from edit time** (default), and a marked look-ahead research mode. Live Monitor reports actual per-source MTProto connection state and provides evidence from each observed revision, with explicit source-overlap indicators rather than ungrounded plagiarism verdicts. See `docs/TALOS_V472_TELEGRAM_EDIT_AUDIT.md` for details and limitations. Backtest data availability still depends on the archived source and external market data.

# Talos v4.7.1 — Auditable Sentiment Replay

New: deterministic versioned sentiment rules, frozen point-in-time headlines, durable replay jobs, missed profitable hypothetical trades, monthly metrics, CSV audit, and no live news auto orders. See `docs/TALOS_V47_SENTIMENT_BACKTEST_AR.md` and `docs/TALOS_V47_DEPLOY.md`.

---

# Talos 4.6.0: Durable Jobs + News-to-Chart Strategy

This is the complementary frontend GitHub upload (not a separate standalone deployment).
**Upload both batches, backend first, frontend second, into the root of the SAME repository.**

See `docs/TALOS_V46_DEPLOY.md` for Render configuration and `docs/TALOS_V46_NEWS_TRADING.md` for conservative risk rules. There is no autonomous live-money execution in the new News Strategy module.

- Backend archive: 97 runtime files in `backend/` + root `Dockerfile` and `render.yaml` = 99 files.
- Frontend archive: updated React UI and assets plus repository metadata and documentation (<100 files).
- Root Dockerfile builds and serves the frontend with the backend. Avoid nested top-level folders.
- Required Render env: `DATABASE_URL`, existing `SECRET_KEY`, `EMBEDDED_JOB_WORKER=true`, `EMBEDDED_LIVE_WATCHER=true`, `LIVE_TRADING_ENABLED=false`.
- Telegram: configure TELEGRAM_NOTIFY bot token and private chat ID in Admin; send /start. Optional `TELEGRAM_PAPER_COMMANDS_ENABLED=true` for *paper* command only.

Validation: 36 backend unit tests pass; 84 static API call matches; TSX parses successfully; full docker/npm build and third-party services were not available for end-to-end testing.


### v4.7.1 notification improvements

The automatic **late-news sentiment backtest** now records both profitable and losing missed simulated trades in a durable `news_missed_alerts` PostgreSQL outbox and submits each one to the existing notification bot. Two independently configurable flags (`missed_wins`, `missed_losses`) default to true. Replays initiated manually are report-only, not a historical alert flood. Delivery counts can be inspected in Admin. Historical OHLCV simulation is not a live or paper fill, and real-money news orders remain disabled.


Talos v4.7.4: Telegram replay parser recognizes T1-T5 and keycap targets, and reruns historical signal text via backend.
