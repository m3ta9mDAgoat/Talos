from fastapi import APIRouter

from app.core.config import settings
from app.core.enums import ResearchStage
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
