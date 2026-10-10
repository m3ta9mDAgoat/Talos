from __future__ import annotations

import asyncio
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    analysis, audit, auth, backtests, exchanges, jobs, live, optimizer, paper,
    processing, signals, sources, strategies, studio, system, research,
    settings as settings_routes, telegram_audit, osint, news_trading,
)
from app.core.config import settings
from app.db.init_db import init_db
from app.intelligence.collect import intelligence_loop
from app.services.jobs import worker_loop
from app.services.notifications import telegram_paper_command_loop
from app.services.news_trading import paper_monitor_loop, sentiment_replay_loop
from app.scripts.universal_worker import watcher_loop
from app.services.security import SESSION_COOKIE, admin_exists, validate_session

VERSION = "4.7.6"
BUILD_ID = "talos-v4.7.6-adaptive-signal-horizon"

_runtime_lock = threading.Lock()
_runtime_threads: dict[str, threading.Thread] = {}
_database_ready = False
_database_error: str | None = None


def _start_async_daemon(name: str, factory: Callable[[], Awaitable[None]]) -> None:
    """Run long-lived Talos workers outside FastAPI's event loop.

    Render routes health checks and HTTP traffic through the Uvicorn event loop.
    A long Telegram/process/backtest cycle must never block that loop.  Each
    embedded worker therefore owns a dedicated daemon thread and asyncio loop.
    """
    with _runtime_lock:
        current = _runtime_threads.get(name)
        if current and current.is_alive():
            return

        def runner() -> None:
            try:
                asyncio.run(factory())
            except BaseException as exc:
                # Keep failures visible in Render runtime logs.  The web server
                # remains available even if a background runtime crashes.
                print(f"[talos-runtime:{name}] stopped: {type(exc).__name__}: {exc}", flush=True)

        thread = threading.Thread(target=runner, name=f"talos-{name}", daemon=True)
        _runtime_threads[name] = thread
        thread.start()
        print(f"[talos-runtime:{name}] started", flush=True)


def _try_init_database() -> bool:
    global _database_ready, _database_error
    try:
        init_db()
        _database_ready = True
        _database_error = None
        return True
    except Exception as exc:
        _database_ready = False
        _database_error = f"{type(exc).__name__}: {exc}"
        print(f"[talos-db] unavailable: {_database_error}", flush=True)
        return False


async def _database_recovery_loop() -> None:
    # Keep the web/static frontend online even if a hosted DB quota is exhausted.
    # Recovery checks are deliberately sparse so they do not create a new compute
    # polling problem when the database is available again.
    while not _database_ready:
        await asyncio.sleep(900)
        if _try_init_database():
            if settings.embedded_job_worker:
                _start_async_daemon("jobs", lambda: worker_loop())
            if settings.embedded_live_watcher:
                _start_async_daemon("watcher", lambda: watcher_loop())
            _start_async_daemon("osint", lambda: intelligence_loop())
            _start_async_daemon("telegram-paper-commands", telegram_paper_command_loop)
            _start_async_daemon("news-paper-monitor", paper_monitor_loop)
            _start_async_daemon("news-sentiment-replay", sentiment_replay_loop)
            return


@asynccontextmanager
async def lifespan(_: FastAPI):
    # A Neon quota/network outage must not turn the whole site into a Render 502.
    # Serve the frontend in degraded mode and recover the DB workers automatically.
    if _try_init_database():
        if settings.embedded_job_worker:
            _start_async_daemon("jobs", lambda: worker_loop())
        if settings.embedded_live_watcher:
            _start_async_daemon("watcher", lambda: watcher_loop())
        _start_async_daemon("osint", lambda: intelligence_loop())
        _start_async_daemon("telegram-paper-commands", telegram_paper_command_loop)
        _start_async_daemon("news-paper-monitor", paper_monitor_loop)
        _start_async_daemon("news-sentiment-replay", sentiment_replay_loop)
    else:
        _start_async_daemon("db-recovery", lambda: _database_recovery_loop())
    yield
    # Daemon threads intentionally terminate with the container process.  Jobs
    # are durable in SQL and are recovered from checkpoints after a restart.


app = FastAPI(title=settings.app_name, version=VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"],
)

# Talos is a single-user research terminal. Normal research/read/backtest workflows
# stay usable without an admin session. Secrets, private broker balances and live
# execution are admin-only.
_ADMIN_PREFIXES = (
    "/api/v1/settings",
    "/api/v1/live",
    "/api/v1/exchanges/connections",
    "/api/v1/osint/admin",
    "/api/v1/news-trade/admin",
    "/api/v1/news-trade/preview",
)


@app.middleware("http")
async def protect_admin_api(request: Request, call_next):
    path = request.url.path
    is_source_secret = path.startswith("/api/v1/studio/sources/") and path.endswith("/secret")
    if any(path.startswith(prefix) for prefix in _ADMIN_PREFIXES) or is_source_secret:
        if not admin_exists():
            return JSONResponse(status_code=428, content={"detail": "Talos admin setup required"})
        if not validate_session(request.cookies.get(SESSION_COOKIE)):
            return JSONResponse(status_code=401, content={"detail": "Admin authentication required"})
    return await call_next(request)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
        "version": VERSION,
        "database": "ready" if _database_ready else "unavailable",
        "database_error": None if _database_ready else _database_error,
        "job_worker": bool(_runtime_threads.get("jobs") and _runtime_threads["jobs"].is_alive()),
        "live_watcher": bool(_runtime_threads.get("watcher") and _runtime_threads["watcher"].is_alive()),
        "osint_collector": bool(_runtime_threads.get("osint") and _runtime_threads["osint"].is_alive()),
        "sentiment_replay_worker": bool(_runtime_threads.get("news-sentiment-replay") and _runtime_threads["news-sentiment-replay"].is_alive()),
    }


@app.get("/api/public/build")
async def build_info() -> dict:
    return {
        "version": VERSION,
        "build_id": BUILD_ID,
        "features": [
            "durable_jobs", "telegram_historical_replay", "universal_sources",
            "paper_research_upload", "market_charts", "generic_strategy_backtests",
            "optimizer", "paper_trading", "live_exchange_adapters", "secure_admin_vault",
            "telegram_notification_bot", "live_signal_simulation_watcher",
            "isolated_background_runtime", "telegram_event_stream", "telegram_edit_version_archive", "telegram_delete_audit", "telegram_integrity_score", "neon_idle_saver", "database_degraded_mode", "intelligence_multisource", "intelligence_revision_audit", "osint_point_in_time_features", "osint_signal_links", "durable_progress_across_pages", "news_trade_preview", "news_paper_risk_strategy", "news_trade_telegram_commands", "sentiment_rule_versioning", "missed_trade_counterfactual", "durable_sentiment_backtests",
        ],
    }


app.include_router(osint.router, prefix="/api/v1")
app.include_router(news_trading.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(settings_routes.router, prefix="/api/v1")
app.include_router(system.router, prefix="/api/v1")
app.include_router(analysis.router, prefix="/api/v1")
app.include_router(sources.router, prefix="/api/v1")
app.include_router(signals.router, prefix="/api/v1")
app.include_router(backtests.router, prefix="/api/v1")
app.include_router(processing.router, prefix="/api/v1")
app.include_router(exchanges.router, prefix="/api/v1")
app.include_router(paper.router, prefix="/api/v1")
app.include_router(live.router, prefix="/api/v1")
app.include_router(strategies.router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")
app.include_router(telegram_audit.router, prefix="/api/v1")
app.include_router(optimizer.router, prefix="/api/v1")
app.include_router(studio.router, prefix="/api/v1")
app.include_router(research.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(studio.hooks)

# Resolve bundled frontend independently of Render's working directory.
# The root Dockerfile copies the production React build into /app/static.
_static = Path(os.getenv("TALOS_STATIC_DIR", str(Path(__file__).resolve().parents[1] / "static"))).resolve()
_frontend_index = _static / "index.html"
if _frontend_index.is_file():
    app.mount("/", StaticFiles(directory=str(_static), html=True), name="frontend")
else:
    print(
        f"[talos-frontend] MISSING React build: expected {_frontend_index}. "
        "Render must use repository-root Dockerfile (./Dockerfile), "
        "not backend/Dockerfile or a bare uvicorn command.",
        flush=True,
    )

    @app.get("/", include_in_schema=False)
    async def missing_frontend():
        return PlainTextResponse(
            "Talos API is online, but the dashboard build is missing. "
            "In Render select Docker runtime, blank Root Directory, "
            "Dockerfile Path ./Dockerfile and Docker Build Context .; "
            "redeploy latest commit. Check /health and /api/public/build for diagnostics.",
            status_code=503,
        )
