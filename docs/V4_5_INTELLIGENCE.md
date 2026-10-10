# Talos v4.5 — Universal OSINT Intelligence

## Actual scope

This release runs on **the existing Render web service and Neon PostgreSQL database**. It does not create any provider accounts, billable cloud resources, or execute live trades. Existing Telegram audit, replay, strategy studio, optimizer, paper execution and admin modules remain in place. `LIVE_TRADING_ENABLED=false` remains the safe default.

### Source adapters shipped

| Adapter | Connected source families | Important limitations |
| --- | --- | --- |
| RSS / Atom | Federal Reserve, ECB, BLS, SEC announcements and filings, CISA, Coinbase system status, BBC, UN News, EIA, crypto publishers | Each publisher can block Render or change its feed; status is shown. |
| GDELT DOC API | Global macro and geopolitical articles | Aggregated media coverage, **not** authoritative confirmation. API rate limits apply. |
| GitHub public releases | Bitcoin, Ethereum client, Solidity, OpenAI Python SDK | Releases are public and rate-limited; commits and every repository are **not** monitored. |
| Exchange HTML | Binance, OKX, Bybit official announcements | Best-effort links; page layout, region restrictions or bot protection can interrupt coverage. Uses public pages only. |
| Mempool public API | Bitcoin estimated fee metrics | Network measurements, **not news** or executable signals. |

OSINT covers multiple asset classes (crypto, stocks, FX/macro, commodities, geopolitics, cyber and technology) **only to the extent that these public data sources cover them**. This is not a comprehensive paid market-data or global OSINT feed.

### Collection controls / zero-spend

- Starts 35 s after backend startup, with hourly collection rounds and individual source intervals of 45–360 minutes. Readily modified in `app/intelligence/sources.py` if limits permit; default favors Neon free-tier conservation, not second-by-second breaking-news detection.
- Each source has a persisted, cross-process SQL claim lease; collector failures are visible through `/api/v1/osint/sources`.
- Maximum 45 articles parsed from one RSS; 35 from one GDELT request; 5 releases per GitHub repository; 30 exchange links. Upstream HTTP response size <= 2 MB and 9 s timeout per source.
- New event archiving **stops at 7,500 events**; no critical records are silently deleted. The dashboard reports capacity, but this is not a guarantee that the entire Neon database stays under its free quota—other tables and revisions still use space.
- No paid API keys, no paid LLM calls, no auto-upgrade, no new Render instance. **Provider-side limits can still suspend collection**; budgets are not hard caps if an account was upgraded to a metered tier.
- No arbitrary URL crawling on the new OSINT collector. HTTP scraping uses a curated allowlist of public pages; no bypass of paywalls, authentication, or publisher restrictions.

### Database entities

- `intelligence_events`: normalized current article, source, categories, symbols, region, first observed time, publication time (if known), classification priority, confidence **in event classification**, not confidence that the headline is true.
- `intelligence_revisions`: immutable first and observed follow-up snapshots of article title/summary. Correction history begins when the source is first observed by this Talos installation; it does not recover pre-observation edits.
- `intelligence_source_state`: last attempt/success/errors, enabled flag, atomic claim lease.
- `intelligence_signal_links`: asset overlap with recent Talos signals; linking is contextual, **not causal or predictive**. Late-arriving signals are matched on collection rounds.
- `intelligence_indicators`: distinct on-chain values with an observed-at timestamp.

Old `osint_items` data remains untouched. A one-time idempotent import (after the web server starts, on the collector thread) copies up to 2,000 existing v4.4 feed observations into the intelligence timeline while preserving the legacy first-seen timestamp; it does not synthesize missing earlier revisions. No destructive migration is applied to existing Talos tables. Existing credential, signal, source and trading data remains as-is.

### Read-only API

| Endpoint | Function |
| --- | --- |
| `GET /api/v1/osint/dashboard` | Aggregated paper/trading/collector headline KPIs. |
| `GET /api/v1/osint/feed?limit=25&category=crypto&important_only=true` | Ranked, cluster-deduplicated event feed. Filters: category, source, symbols, query, window_hours and deduplicate. |
| `GET /api/v1/osint/events/{id}` | Source metadata, first observation, all retained observed revisions, signal links. |
| `GET /api/v1/osint/sources` | Adapter roster and last success/error. |
| `GET /api/v1/osint/indicators` | Latest measured network fee readings. |
| `GET /api/v1/osint/features?symbol=BTC&lookback_hours=24&as_of=...` | Machine-readable research features using original/observed revisions *as known at as_of*. |

Admin authentication (existing Talos setup/session cookie) is required for:

- `PUT /api/v1/osint/admin/sources/{key}` body `{"enabled": false}` / `{"enabled": true}`.
- `POST /api/v1/osint/admin/refresh/{key}` — throttled to once per source every 15 minutes; background-only.

### Rule-based OSINT backtest features

Existing Technical Strategy definitions may include:

- `osint_event_count` — public observed headlines mentioning the base asset within lookback.
- `osint_high_count` — such events with rule-based priority >= 80.
- `osint_max_importance` — highest priority within lookback, or 0 only when sufficient observation history exists.

Example, editable through **Strategies → Load OSINT strategy example**:

```json
{
  "symbols": ["BTC/USDT"],
  "timeframe": "1h",
  "osint_lookback_hours": 24,
  "entry_logic": "all",
  "conditions": [
    {"feature": "osint_high_count", "op": ">=", "value": 1},
    {"feature": "volume_ratio", "period": 20, "op": ">", "value": 1.5}
  ],
  "take_profit_pct": 4,
  "stop_loss_pct": 2,
  "max_holding_bars": 24
}
```

The OSINT feature loader uses one bounded database batch per strategy/symbol run, **not one database query per bar**. For reproducibility it uses the first archived revision only in the precomputed bar context. The REST `/features` endpoint supports revisions as known at any `as_of` time. Missing pre-archive history is **unknown**, not zero, and comparisons fail closed. News cannot be claimed to cause price moves simply because symbol names match.

### Feed ranking and safety

Deterministic keyword-rule event classification and fixed editorial source reliability scores. No fabricated confidence scores from an ML model, no prediction probabilities, no real order calls. Duplicate links are suppressed per source and near-identical titles are clustered in feed responses. A publisher article update is archived as another observed revision. Features are not asserted to have validated out-of-sample predictive value.

### Known limitations (important)

1. **Render Free sleeps.** Collection, Telegram watcher, alert delivery and background jobs are not genuinely 24/7 on a sleeping service. No architectural migration is attempted here.
2. **Provider connectivity is not verified** from this isolated build environment. Sources showing `unavailable` are truthful error states, not silent fallback to invented headlines.
3. Vite production compilation requires dependencies from npm; in this environment npm DNS resolution is unavailable. TSX syntax was checked but full production frontend bundling must be verified by Render CI/build.
4. Existing Talos modules (brokers, order reconciliation, alerts, data vendors) inherit their own historical limitations; this release does not claim production-grade live execution guarantees.
5. Some public feeds restrict commercial redistribution or scraping. Review provider usage terms before expanding sources or publishing aggregated content.

### Local verification

```bash
cd backend
pip install -r requirements.txt
pytest -q
python -m compileall -q app
cd ../frontend
npm install
npm run build
```

Confirmed locally in artifact production: 30 Python tests pass; TSX syntax can be transpiled. Network-dependent build and real Neon/Render deployment are not verified.
