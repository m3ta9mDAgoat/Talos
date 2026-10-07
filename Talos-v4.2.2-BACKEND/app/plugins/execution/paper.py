from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.plugins.contracts import Decision, ExecutionBroker


class PaperBroker(ExecutionBroker):
    """Execution-compatible paper broker used before any live connector is enabled."""

    async def place(self, decision: Decision, **kwargs: Any) -> dict[str, Any]:
        return {
            "id": f"paper-{uuid4().hex}",
            "status": "accepted",
            "mode": "paper",
            "symbol": decision.symbol,
            "side": str(decision.side),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": kwargs,
        }

    async def cancel(self, order_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"id": order_id, "status": "cancelled", "mode": "paper"}
