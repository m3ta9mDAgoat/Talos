from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

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
from app.services.security import SESSION_COOKIE, admin_exists, validate_session

VERSION = "4.0.0"
BUILD_ID = "talos-v4-durable-research-execution"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    stop = asyncio.Event()
    worker = asyncio.create_task(worker_loop(stop)) if settings.embedded_job_worker else None
    try:
        yield
    finally:
        if worker is not None:
            stop.set()
            worker.cancel()
            try:
                await worker
            except BaseException:
                pass


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
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env, "version": VERSION}


@app.get("/api/public/build")
async def build_info() -> dict:
    return {
        "version": VERSION,
        "build_id": BUILD_ID,
        "features": [
            "durable_jobs", "telegram_historical_replay", "universal_sources",
            "paper_research_upload", "market_charts", "generic_strategy_backtests",
            "optimizer", "paper_trading", "live_exchange_adapters", "secure_admin_vault",
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
