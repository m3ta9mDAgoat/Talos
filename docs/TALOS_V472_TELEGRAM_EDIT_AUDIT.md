# Talos v4.7.2 — Telegram Replay and Live Revision Evidence

## 1. Why Telegram Replay previously showed 100% and zero results
A finished task used to display `complete` even when `signals_eligible=0`; an empty run now reports a **NO DATA** outcome and explicit diagnostic counters. The user must check source archive (`Raw events`), classification (`Revisions processed`), successfully parsed BUY signals, missing entry/targets, and whether historical market data exists. If the message has never been archived, Talos cannot invent a signal.

## 2. Replay edited signals without look-ahead
**Telegram Replay → Edited-message replay policy**:
- **Original only**: use the originally observed parseable version; if its revision was not recorded, the original terms cannot be inferred.
- **Include edited signals (replay from edit time)**: edited parameters are allowed and become effective at the recorded edit timestamp. This is the normal mode for including edits without trading them retroactively. `exclude_edited=false`.
- **Research only / look-ahead**: applies edited terms at the original post time, an intentionally biased hypothetical; **not** a legitimate measured outcome.

Backtests currently handle spot BUY only; missing parseable entry/targets or unavailable market data still prevents measured outcomes. Each completed job includes exclusion counts. The Start button will not send a replay when readiness shows no eligible BUY signal. Duplicate job submission reuses the same durable job for the same settings/input snapshot; import/process jobs also reuse an active job.

## 3. Capture from the next live event
When the backend starts and `EMBEDDED_LIVE_WATCHER=true`, the enabled Telegram source connects to MTProto (requires a valid Telegram session and access to the channel). The stream subscribes to `NewMessage`, `MessageEdited`, `MessageDeleted`. The `Live Monitor` shows **per-source stream connected** state, last connection timestamp, last received event/edit, and errors. These are observations of the server process, not an SLA. A disconnected stream or sleeping/free Render means Talos may miss intermediate edits. A later catch-up might recover the latest snapshot but not prior versions. Historical imports usually show only the latest edited snapshot; they do **not** reveal the original.

The first **parseable** live message starts its paper simulation when actually observed; if it became parseable only after an edit, it does not get a retroactive live entry based on the old post timestamp. Existing live simulations track edited targets prospectively and preserve already-hit targets, but this system is a research simulator, not exchange execution.

## 4. Changes, suspicious changes and provenance
`Live Monitor → Telegram revision audit → Inspect evidence` displays an immutable chronological record (where observed): first text, each later version, first-seen timestamp, Telegram edit timestamp, extracted entry/stop/targets/symbol/side, exact differences and severity labels. Categories:
- `text_or_context_edit`: formatting/context changed, no parsed trading level change.
- `risk_or_target_edit`: stop or TP changed.
- `material_signal_edit`: symbol/side/entry changed after publication.
- `signal_unparsable_after_edit`: a prior parsed signal was changed to text the parser can't interpret.
- `original_not_observed`: only an already-edited snapshot was fetched; never claim to know prior text.

For unusually distinctive text only (>=85 normalized characters and >=12 unique words), the inspector checks up to 1500 previous observed events from other monitored Telegram channels over 7 days, using token overlap >=0.72 and text similarity >=0.88. A match is merely **earlier similar text**, with evidence timestamps and the other channel. Generic trading templates are not enough. Metadata such as Telegram's native forward origin is shown when available. Talos cannot determine the original author, permission, plagiarism, or intent from an edit or textual match. A provider can copy with permission, and an earlier post is not necessarily the original source.

## 5. Deployment and validation
Upload **backend ZIP contents** to the existing GitHub repository root, then **frontend ZIP contents** to that same root. Do not upload a ZIP itself. Render uses the root `./Dockerfile`. `/health` should say v4.7.2, DB ready, job worker and live watcher running. In **Live Monitor**, verify stream **CONNECTED** separately; worker online does not prove an MTProto connection. `telegram-audit/sources/{source_id}/watcher` provides per-source state.

No connected production Telegram test, real Render deployment, full Vite build or live market backtest has been performed in this environment. Unit tests and static API/syntax checks do not guarantee actual message delivery or public exchange candle availability. Do not enable automated live orders based solely on these tests.
