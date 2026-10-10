from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import ResearchStage
from app.db.session import get_db
from app.services.lifecycle import can_transition

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/capabilities")
async def capabilities() -> dict:
    return {
        "data_sources": ["telegram", "news", "rss", "market", "custom"],
        "markets": ["crypto_spot"],
        "future_markets": ["crypto_futures", "forex", "stocks", "commodities", "mt5"],
        "research_stages": [stage.value for stage in ResearchStage],
        "portable_deployment": True,
        "multi_source_strategies": True,
        "optimization_aware_plugins": True,
        "live_trading_enabled": settings.live_trading_enabled,
    }


@router.get("/lifecycle-check")
async def lifecycle_check(current: ResearchStage, target: ResearchStage) -> dict:
    return {"allowed": can_transition(current, target)}


@router.get("/database/usage")
def database_usage(db: Session = Depends(get_db)) -> dict:
    """Small, on-demand DB footprint report. Never polled by background workers."""
    dialect = db.bind.dialect.name if db.bind is not None else "unknown"
    if dialect == "postgresql":
        total = int(db.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0)
        rows = db.execute(text("""
            SELECT c.relname AS table_name, pg_total_relation_size(c.oid) AS bytes
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r' AND n.nspname = 'public'
            ORDER BY bytes DESC
            LIMIT 15
        """)).mappings().all()
        return {
            "dialect": dialect,
            "database_bytes": total,
            "database_mb": round(total / 1024 / 1024, 2),
            "top_tables": [
                {"table": str(r["table_name"]), "bytes": int(r["bytes"]), "mb": round(int(r["bytes"]) / 1024 / 1024, 2)}
                for r in rows
            ],
        }
    if dialect == "sqlite":
        page_count = int(db.execute(text("PRAGMA page_count")).scalar() or 0)
        page_size = int(db.execute(text("PRAGMA page_size")).scalar() or 0)
        total = page_count * page_size
        return {"dialect": dialect, "database_bytes": total, "database_mb": round(total / 1024 / 1024, 2), "top_tables": []}
    return {"dialect": dialect, "database_bytes": None, "database_mb": None, "top_tables": []}
