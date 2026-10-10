# Talos v4.5.1 — compatibility audit

## Scope
- Complete application: React 19 + TypeScript frontend, FastAPI/Python backend, Render root Dockerfile, Neon/PostgreSQL, optional Docker Compose, configuration and documentation.
- Compared frontend HTTP method+URL calls against route decorators on the included backend, not against a different or previously deployed version.

## Verified in the preparation environment
1. **30/30 backend tests passed**, run with `pytest -q` against the included backend test files.
2. **Python backend syntax compiled** without errors with `compileall` (local Python interpreter; deploy target is Python 3.12).
3. **74 literal frontend API calls matched backend methods/routes** with no unmatched calls in a source-level routing audit. This does not guarantee equivalent request or response payload semantics.
4. **Frontend TS/TSX syntax parsed** with locally available TypeScript transpiler; full `tsc -b && vite build` could not run because the package registry was unreachable.
5. **Original blocker fixed:** missing `import os` in `backend/app/main.py` before `os.getenv(TALOS_STATIC_DIR)`; that would have caused `NameError` on application import.
6. **Docker Compose browser API fix:** added `frontend/nginx.conf` to reverse proxy `/api/` and `/health` to backend:8000 and copied it in the frontend Dockerfile. Previously the production-built frontend used relative URLs that hit Nginx with no API forwarding.
7. **Vite development auth fix:** new `frontend/vite.config.ts` proxies `/api` and `/health` to localhost:8000; frontend default uses same-origin requests to preserve admin session cookies.
8. **Version coherence:** backend `/health` and `/api/public/build`, frontend manifest, UI badge all identify v4.5.1.
9. **Render layout checked:** root multi-stage Dockerfile builds frontend and copies `dist/` into `/app/static`; FastAPI `StaticFiles` mounts the SPA at `/` when built. `render.yaml` points to root Dockerfile and health route.

## Not tested; cannot guarantee without external execution
- **`npm install`, TypeScript type checking, `vite build`**: npm registry DNS/network access was unavailable in this environment. Consequently frontend bundling remains unverified.
- **Docker image build and Nginx runtime**: Docker executable is unavailable here.
- **Live Render/Neon connectivity, Telegram, exchange brokers, API rate limits, live OSINT ingestion and end-to-end live order behavior**: need real environment/credentials. Live trading stays off by default.
- The API audit matches URLs + HTTP methods; it does not establish full schema/data compatibility under every path. Do a deployed browser smoke test before considering production ready.

## On Render
Set **Root Directory** blank, **Dockerfile Path** `./Dockerfile`, **Docker Build Context** `.`, **Health Check** `/health`. Environment: `APP_ENV=production`, `DATABASE_URL=postgresql+psycopg://...`, a unique long `SECRET_KEY`, `LIVE_TRADING_ENABLED=false`. Preserve your previous encrypted-vault `SECRET_KEY` when upgrading, otherwise stored credentials will no longer decrypt. Avoid publishing real `.env` values.

Validate after build: `GET /` = React HTML, `GET /talos-splash-2x.png` = PNG, `GET /health` = JSON version 4.5.1, and `GET /api/public/build` = JSON version 4.5.1. Test admin setup/authentication and a non-trading API request in the browser.

## Local checks
```sh
# Python and isolated tests
cd backend
pip install -r requirements.txt
pytest -q

# React (Node 22.12+)
cd ../frontend
npm install
npm run build

# Combined local deployment if Docker is available
cd ..
docker compose up --build
```
Run Compose with a local `.env` based on `.env.example`. Frontend at `http://localhost:3000`, backend at `http://localhost:8000`.
