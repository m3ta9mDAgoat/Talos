# Talos v4.7.8 — Compatibility and verification report

## Implemented

- Preserved the existing SQL schemas and service route names. No destructive data migration.
- Telegram Replay job now evaluates **exactly one signal per step**, and persists each result to `signal_backtests` before updating progress.
- Safe atomic job claim across parallel worker instances; prior working jobs can resume after stale-heartbeat recovery.
- Archive candidate set and completed IDs cached within the process and reconstructed from persistent records on restart; full aggregation only every 50 trades / at finalization.
- `Jobs`, `Operations`, `Sources` and in-page replay jobs use the same job badge with ETA estimates or an honest `ETA calculating` message.
- BingX public CCXT spot market-data resolution, candle retrieval, fallback, configuration and UI selection are enabled. Exchange selection is sequential and takes the first available venue with actual candles.
- Replay deduplication fingerprint bumped to v4.7.8.

## Local tests and results

| Verification | Result |
|---|---|
| Python package bytecode compilation | PASS |
| TypeScript/TSX syntax parsing (`transpileModule` with original filenames) | PASS |
| Two-signal SQL Replay checkpoint, progress, completion and cancellation | PASS |
| 56-trade SQL replay, exactly one in-flight trade | PASS |
| Resume after clearing both in-process caches, no repeated row | PASS |
| Only first / 50th / final full summary runs for 56 trades | PASS (3 summaries) |
| ETA estimated from persisted `work_completed` and `work_total` | PASS |
| Two simultaneous workers try to claim same queued job | PASS (only one executes) |
| Sequential venue fallback (try exchange 2 only after exchange 1 has no candles) | PASS |
| BingX CCXT spot filtering and historical OHLCV adapter with mocked exchange | PASS |
| React/Vite full build | NOT TESTED (dependencies unavailable) |
| Actual Render/Neon/Telegram/BingX network integrations | NOT TESTED |

These tests verify functionality of local code and mocked adapters, not exchange price accuracy or performance at production scale.

## Operational expectations

- Before the first trade completes, the job exposes the preparation/current market-data stage but its **ETA is not yet available**. After enough successful per-trade checkpoints, ETA is approximate: different symbols and exchange fallback paths can take substantially different amounts of time.
- Persisted result rows are the source of truth. Cancelling the old #22 job does not delete source Telegram messages.
- A trade with unavailable candles records `no_market_data`; this is not a winning or losing trade. Examine individual `fetch_errors` for the venue that failed.
- If the proxy continues to return 502, Render logs and Events are needed to distinguish process OOM, network timeout, application exception or database outage. This software patch is not a guarantee against provider limitations.
