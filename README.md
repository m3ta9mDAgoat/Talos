# Talos v4.7.8 — Sequential Telegram Replay, ETA and BingX

This release builds on the v4.7.7 replay-worker optimizations and retains the existing parser, Telegram archive, adaptive live-simulation horizon, and notification system.

## Changes

- **One trade at a time:** the Telegram Replay worker evaluates and commits one candidate per worker cycle. It does not download market history for 12 signals concurrently.
- **Durable per-trade progress:** each evaluated row is committed to SQL before the job advances. After a process restart, the worker skips previously committed signal IDs. The first eligible-signal preparation phase and market-data stage are visible in the job status.
- **Safe multi-instance claiming:** a single atomic SQL update claims each queued/retrying job so that two worker instances do not execute the same job at once.
- **ETA on every job badge:** the UI shows an estimated time remaining for any running job with sufficient progress; Telegram Replay calculates it from real observed per-trade throughput. When there is insufficient information, the UI says 'ETA calculating…' or 'ETA measuring per-trade speed…', rather than displaying fabricated times. ETA is indicative and can change when exchanges delay or reject requests.
- **No expensive full history aggregation after every trade:** aggregate replay metrics are calculated on the first result, every 50 committed trades and at completion. The in-progress trade count is updated after each trade.
- **BingX:** added `bingx` to CCXT public spot-market resolution and exchange fallback defaults, exchange status/market endpoints, vault config, and exchange selectors. The real historical coverage depends on listings and BingX API availability.
- Updated job fingerprint to `v4.7.8` so previously completed `NO DATA` jobs are not incorrectly reused.

## Deployment

Both ZIP files are meant to be extracted **into the root of the same GitHub repository**, not as two subfolders. Upload the backend ZIP contents followed by the frontend ZIP contents. Keep the Render Root Directory blank, use `./Dockerfile`, and wait for the service to redeploy.

**Important: Cancel the old stuck Replay job (#22 in the screenshot) using Jobs → Cancel before starting a new replay.** The former job is not automatically migrated to the new per-trade execution policy. Cancelling a job doesn't delete archived Telegram messages.

1. Deploy both ZIP files and confirm `/health` has version `4.7.8`.
2. In Jobs confirm the SQL worker is online, cancel an old blocked Telegram Replay if it remains active, and start a new Replay from the Telegram Replay page.
3. Watch **Operations** for `Preparing eligible Telegram signals`, then `Market data · trade N/Total · SYMBOL`. After two trades with measured throughput, an ETA should appear. The number and ETA also appear in Jobs and source operation cards.
4. The job stores each tested trade (or an explicit `no_market_data` result) in SQL. You can leave the page and return later.
5. For exchange issues inspect the individual trade's `fetch_errors` and Render runtime logs. Public-market candles need no BingX API key. Private BingX actions require credentials and the existing explicit live-trading gate.

## Validation scope

Verified locally: Python bytecode compilation, TypeScript/TSX syntax parsing, database-backed two-trade replay and cancellation, 56 sequential trade checkpoints, cache eviction/restart recovery, concurrent worker job claiming, repeated full-summary avoidance, ETA based on actual recorded work, sequential exchange fallback, and a mocked CCXT BingX public spot/candles adapter.

**Not verified:** actual Render or Neon deployment, exchange availability and historical MAGIC/CTSI candle coverage on BingX, the live Telegram API, and a production 4,206-trade run. A live Render `HTTP 502` can also be caused by unrelated hosting limits, proxy timeouts, database/network failures or restarts. If that happens, check Render Events and Logs. The full npm/Vite build was not run because the current environment does not have this project's React/Vite dependencies installed.
