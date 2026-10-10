# Talos v4.5.0 — Universal Intelligence + Command Center

- Preserves Render + Neon deployment and all existing trading/Telegram/research modules.
- Preserves original user's splash art exactly (unchanged file bytes).
- Redesigned minimal Command Center and new Intelligence page with filters, source health, audited revisions and measured on-chain indicators.
- Generalized public-source adapters: RSS/Atom, GDELT, GitHub releases, best-effort official exchange HTML announcements, mempool fee metric.
- Added normalized OSINT event tables, revisions, source leases, symbol links, indicator table, context matching for late-arriving Telegram signals, and non-destructive one-time legacy v4.4 OSINT import.
- Added queryable replay-safe OSINT features and Technical Strategy bar features (counts, priority, lookback), failing closed if historical coverage absent.
- Added source pause/resume and rate-limited admin refresh.
- Reduced collector loop frequency to hourly to conserve Neon compute; capped new OSINT events at 7,500.
- Added OSINT tests; previous backend test suite remains passing (30 tests total).
- Full frontend production build and provider connectivity remain unverified in this offline tool environment.
