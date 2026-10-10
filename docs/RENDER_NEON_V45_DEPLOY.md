# Deploy v4.5 to existing Render + Neon

1. **Backup first.** Save the last functioning ZIP / Git commit. Keep the existing Neon database and Render service; do not create new paid products.
2. Extract `Talos-v4.5.0-Full-OSINT-GitHub-ready.zip` into the **repository root**, not into an extra nested `talos_v44` folder. Preserve all Render environment values. Do not upload `.env`, credential files, sessions, or API keys to GitHub.
3. Review the change against your actual GitHub main branch, then commit/push through Git. This artifact is based on the v4.4 code available in this conversation, not a fresh authenticated snapshot of private GitHub.
4. Existing `render.yaml` is maintained. The Dockerfile builds React and starts the same FastAPI server. On Render, check that frontend `npm install && npm run build` and Python image installation succeed. A failing dependency build means stop; do not declare deploy complete.
5. On first backend startup, `Base.metadata.create_all` creates **new** `intelligence_*` tables. It does not drop or alter the existing trading, Telegram, or legacy `osint_items` tables. A backup is still advised before any schema change.
6. Confirm `GET /health` reports version `4.5.0`, database `ready`, and `osint_collector=true`. Wait at least 35 seconds before first collector attempt. Source data may require more time due to upstream rate limits and a sleeping Render instance.
7. Check `GET /api/v1/osint/sources`, `GET /api/v1/osint/feed`, `GET /api/v1/osint/dashboard`, and `GET /api/v1/osint/indicators`. Sources marked unavailable require URL/layout/permissions investigation, **not mocked news**.
8. In the browser verify original splash, Command Center, Intelligence page, Signals/Replay, Strategies/Backtests, Paper Trading and Admin. Keep `LIVE_TRADING_ENABLED=false` until live execution has its own independent validation.
9. Check Neon Compute and Storage usage. The in-app 7,500-event cap limits only *new intelligence event rows*, **not** total Neon storage or compute hours. Staying on unupgraded free tiers is the account-level zero-charge safeguard.
10. If anything fails, redeploy the previous Git commit. New `intelligence_*` tables may remain idle; rollback does not require deleting historical data.

## Runtime requirements

- Existing Render Free service, existing Neon DB URL, existing `SECRET_KEY`, and required Telegram secrets as before.
- No paid provider tokens or external API keys are required to attempt the included public-source collectors.
- Render Free sleeping prevents guaranteed around-the-clock collection. This is a deployment limitation, not a software flag that can be fixed for free without changing hosting.
