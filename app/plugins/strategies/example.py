from datetime import datetime, timezone

from app.core.enums import MarketType, Side
from app.plugins.contracts import Decision, Event, Strategy


class ExampleStrategy(Strategy):
    strategy_id = "example"
    version = "0.1.0"

    async def on_events(self, events: list[Event], features: dict) -> list[Decision]:
        return []

    def optimization_space(self) -> dict:
        return {
            "risk_pct": {"type": "float", "low": 0.25, "high": 5.0},
            "max_positions": {"type": "int", "low": 1, "high": 20},
        }
