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
