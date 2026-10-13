# v2.4 status — verified / not verified

- Reference backtesting + PostgreSQL API: covered by end-to-end tests; Render v2.4 deployment PENDING GitHub push.
- Hosted reference engine: runs in Render single-container with small CPU tasks only.
- Phase2: original approved `web/APPROVED_ORIGINAL.html` untouched, SHA256 `65739614032b64d05818c84c82700f6765da6652b734cf2c9b080a2941d015a8`.
- Responsive toolbar and modals: browser tested at 1280 × 800 and 390 × 740.
- The frontend uses real APIs to list jobs/datasets, upload OHLCV, submit strategies, archive Telegram JSON, paper simulate fills, verify and export results.
- Bot heartbeat: added endpoint and a caller in Telegram polling agent. Until an owner executes the process against Render, status remains not_connected.
- Native LEAN/Nautilus Stage3: narrow synthetic fixture parity only, not generic native execution.
- News/social provider, external subscriptions, X/RSS automatic authentication, Phanes autonomous queries, and live brokerage: NOT connected.
- Render PostgreSQL free expires 2026-11-12. Backup/migration needed before expiry.
- Security: no credentials in ZIP. Keep keys in Render Environment or local prompts only.
