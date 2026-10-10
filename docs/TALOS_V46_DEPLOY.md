# Talos v4.6.0 — GitHub / Render Upload (two batches under 100 files each)

1. Extract `Talos-v4.6.0-BACKEND-GitHub-99-Files.zip`. Upload its **contents** to the existing GitHub repository **root**. Paths must include `backend/app/...`, `backend/requirements.txt`, root `Dockerfile`, and root `render.yaml`.
2. Extract `Talos-v4.6.0-FRONTEND-GitHub-Under-100-Files.zip`. Upload its **contents** to that *same root*. Paths must include `frontend/src/main.tsx`, `frontend/src/styles.css`, and `frontend/package.json`.
3. **Do not upload files as a nested enclosing folder** or overwrite your real `.env` or production credentials. Deleting old tests/docs is not necessary; old files can remain in the repo. The limits apply to each upload batch/archive, not total repository files.
4. Render service: Docker runtime, **Root Directory blank**, **Dockerfile Path `./Dockerfile`**, **Context `.`**, health path `/health`.
5. Required Environment: `DATABASE_URL=postgresql+psycopg://...`, `SECRET_KEY` (same as your previous Talos release if the Vault already has encrypted tokens), `EMBEDDED_JOB_WORKER=true`, `EMBEDDED_LIVE_WATCHER=true`, `LIVE_TRADING_ENABLED=false`.
6. Optional Telegram private paper command: `TELEGRAM_PAPER_COMMANDS_ENABLED=true`. Save the notification bot token & private chat ID first in Admin / Vault. Never store tokens in GitHub.
7. Verify `/health` has version `4.6.0`, database `ready`, `job_worker=true`. Check `/api/public/build`, open News Strategy, open Intelligence and inspect source error messages. Try a news review; most archived old items should correctly refuse paper orders.
8. Never enable live-money automation solely because a paper run went positive. News live auto orders are **intentionally not implemented** in v4.6.0. Existing Live Execution remains an independent, explicitly confirmed/admin-only feature.

## Validation limits

Backend: 36 unit tests pass locally, Python syntax compiled. Frontend: TypeScript/TSX syntax parsed. Full `npm install && npm run build`, Docker image, deployed Neon, third-party sources, Telegram, actual exchange quotes or live fills could not be validated in this environment. Test in a staging deployment before production.

## Operational caveats

- Render Free can sleep. SQL jobs persist; they require a running worker process to progress. SQL history survives navigation but not a complete service outage.
- Paper stop/target checks run on the application server about once a minute; not native exchange stops.
- Collector feed intervals are currently measured in tens of minutes/hours. Auto-paper freshness cap defaults to 90 seconds. This is a conservative prototype, not production latency-sensitive news trading.
- No future trade win probability is displayed without a calibrated model; news editorial importance is not a calibrated win chance.
