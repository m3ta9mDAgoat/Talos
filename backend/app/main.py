from __future__ import annotations

import asyncio
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
    settings as settings_routes,
)
from app.core.config import settings
from app.db.init_db import init_db
from app.services.jobs import worker_loop
from app.scripts.universal_worker import watcher_loop
from app.services.security import SESSION_COOKIE, admin_exists, validate_session

VERSION = "4.2.1"
BUILD_ID = "talos-v4.2.1-render-runtime-fix"

_runtime_lock = threading.Lock()
_runtime_threads: dict[str, threading.Thread] = {}


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


@asynccontextmanager
async def lifespan(_: FastAPI):
    # DB schema creation/migrations stay synchronous during startup.  Long-lived
    # workers are started only afterwards and never share Uvicorn's event loop.
    init_db()
    if settings.embedded_job_worker:
        _start_async_daemon("jobs", lambda: worker_loop())
    if settings.embedded_live_watcher:
        _start_async_daemon("watcher", lambda: watcher_loop())
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
        "job_worker": bool(_runtime_threads.get("jobs") and _runtime_threads["jobs"].is_alive()),
        "live_watcher": bool(_runtime_threads.get("watcher") and _runtime_threads["watcher"].is_alive()),
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
            "isolated_background_runtime",
        ],
    }


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
app.include_router(optimizer.router, prefix="/api/v1")
app.include_router(studio.router, prefix="/api/v1")
app.include_router(research.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(studio.hooks)

_static = Path("static")
if _static.exists():
    app.mount("/", StaticFiles(directory=_static, html=True), name="frontend")
