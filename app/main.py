from __future__ import annotations

import asyncio
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    analysis, audit, auth, backtests, exchanges, jobs, live, optimizer, paper,
    processing, signals, sources, strategies, studio, system, research,
    settings as settings_routes, telegram_audit, intelligence,
)
from app.core.config import settings
from app.db.init_db import init_db
from app.intelligence.collect import intelligence_loop
from app.services.jobs import worker_loop
from app.scripts.universal_worker import watcher_loop
from app.services.security import SESSION_COOKIE, admin_exists, validate_session

VERSION = "4.5.0"
BUILD_ID = "talos-v4.5-universal-intelligence"

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
)


@app.middleware("http")
async def protect_admin_api(request: Request, call_next):
    path = request.url.path
    is_source_secret = path.startswith("/api/v1/studio/sources/") and path.endswith("/secret")
    is_intel_admin = path.startswith("/api/v1/intelligence/") and request.method not in {"GET", "HEAD", "OPTIONS"}
    if any(path.startswith(prefix) for prefix in _ADMIN_PREFIXES) or is_source_secret or is_intel_admin:
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
        "osint_collection_enabled": os.getenv("OSINT_ENABLED", "true").lower() in {"true","1","yes","on"},
        "osint_collector": bool(_runtime_threads.get("osint") and _runtime_threads["osint"].is_alive()),
        "osint_collection_enabled": os.getenv("OSINT_ENABLED", "true").lower() in {"true","1","yes","on"},
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
            "telegram_notification_bot", "live_signal_simulation_watcher", "market_intelligence", "osint_provenance", "osint_event_revisions", "osint_context_links", "osint_features",
            "isolated_background_runtime", "telegram_event_stream", "telegram_edit_version_archive", "telegram_delete_audit", "telegram_integrity_score", "neon_idle_saver", "database_degraded_mode", "intelligence_multisource", "intelligence_revision_audit", "osint_point_in_time_features", "osint_signal_links",
        ],
    }


app.include_router(intelligence.router, prefix="/api/v1")
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

_static = Path("static")
if _static.exists():
    app.mount("/", StaticFiles(directory=_static, html=True), name="frontend")
