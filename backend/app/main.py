from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.api.routes import analysis, backtests, signals, sources, system, exchanges, paper, live, processing, strategies, audit, optimizer, auth, settings as settings_routes
from app.core.config import settings
from app.db.init_db import init_db
from app.services.security import SESSION_COOKIE, admin_exists, validate_session


@asynccontextmanager
async def lifespan(_: FastAPI):
    # MVP bootstrap. Alembic migrations replace create_all before production data matters.
    init_db()
    yield


app = FastAPI(title=settings.app_name, version="2.1.1", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def protect_api(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/v1/") and not path.startswith("/api/v1/auth/"):
        if not admin_exists():
            return JSONResponse(status_code=428, content={"detail": "Talos admin setup required"})
        if not validate_session(request.cookies.get(SESSION_COOKIE)):
            return JSONResponse(status_code=401, content={"detail": "Authentication required"})
    return await call_next(request)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env, "version": "2.1.1"}




@app.get("/api/public/build")
async def build_info() -> dict:
    return {
        "version": "2.1.1",
        "build_id": "talos-v2.1.1-fixed",
        "features": [
            "incremental_processing",
            "sql_cached_signals",
            "secure_vault",
            "paper_trading",
            "live_exchange_adapters",
            "public_market_data_backtests",
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


# In the production image the React build is copied to /app/static.
# Mount it last so API and health routes keep priority.
_static = Path("static")
if _static.exists():
    app.mount("/", StaticFiles(directory=_static, html=True), name="frontend")
