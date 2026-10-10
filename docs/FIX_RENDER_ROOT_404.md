# Talos: Render homepage returns `{"detail":"Not Found"}`

## Diagnosis

`GET /health 200 OK` proves the FastAPI service is responding but does not prove the React site has been built or mounted. The homepage `GET / 404` is consistent with the Render service running the backend-only Dockerfile (or a bare Uvicorn command) without `/app/static/index.html`.

## Fix existing Render service — NO infrastructure migration

1. Go to Render > **talos** > **Settings** > Build & Deploy.
2. Check **Runtime: Docker**.
3. Set **Root Directory** to blank (repository root).
4. Set **Dockerfile Path** to `./Dockerfile`, *not* `backend/Dockerfile`.
5. Set **Docker Build Context Directory** to `.` (repository root).
6. Ensure there is **no backend-only start command override** (`uvicorn app.main:app ...`); use Dockerfile's `CMD`.
7. Preserve existing Neon `DATABASE_URL`, `SECRET_KEY` and Telegram environment variables. Do **not** reveal or re-enter secrets in public logs or screenshots.
8. Push the extracted ZIP contents to GitHub **repository root**, not an extra nested folder. Render > Manual Deploy > Deploy latest commit (optionally Clear build cache and deploy if old Docker configuration persists).
9. Confirm Render build output shows **frontend-build** and `npm run build` (Vite). A successful Python-only build is not sufficient.
10. Test `https://<your-render-domain>/` (React page), `/talos-splash-2x.png` (200), `/health` (200 and `database` should be `ready`), and `/api/public/build` (build version). A database outage may leave the public page up with degraded data; health `200` alone is not a database check.

Do not create another Render service or Neon database. Render Free instances can sleep; uptime is not guaranteed.

## Code changes in this patch

- Uses path relative to backend package (`/app/static`) instead of depending on working directory.
- Root Docker build asserts `dist/index.html`, copied `static/index.html` and the original splash image exist.
- If the frontend was not bundled, root returns an explicit `503` deployment misconfiguration message instead of a misleading default `404`; health and API routes remain accessible.
- Leaves user-supplied splash PNGs, dashboard, OSINT collectors, research and trading code unchanged.

## Verification limits

30 existing backend tests passed in the workspace. Direct full API import and frontend compilation were not verified in this workspace (Telethon runtime dependency absent and npm install did not complete). Render deployment and real Neon connection must be verified using the live environment.

Rollback: redeploy the previous known-good Git commit if the release fails. No database migrations are introduced by this patch.
