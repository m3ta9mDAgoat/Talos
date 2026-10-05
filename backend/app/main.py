from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.api.routes import backtests, signals, sources, system
from app.core.config import settings
from app.db.init_db import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    # MVP bootstrap. Alembic migrations replace create_all before production data matters.
    init_db()
    yield


app = FastAPI(title=settings.app_name, version="0.3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "env": settings.app_env, "version": "0.3.0"}


app.include_router(system.router, prefix="/api/v1")
app.include_router(sources.router, prefix="/api/v1")
app.include_router(signals.router, prefix="/api/v1")
app.include_router(backtests.router, prefix="/api/v1")


# In the production image the React build is copied to /app/static.
# Mount it last so API and health routes keep priority.
_static = Path("static")
if _static.exists():
    app.mount("/", StaticFiles(directory=_static, html=True), name="frontend")
