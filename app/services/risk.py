from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RiskDecision:
    approved: bool
    position_fraction: float
    reasons: list[str]
    metadata: dict[str, Any]


class RiskManager:
    """Stable contract shared by paper and future live execution.

    V1 channel research does not need capital access. The risk layer is intentionally
    deterministic so optimization can tune parameters later without letting a strategy
    choose its own capital allocation.
    """

    def evaluate(self, *, requested_fraction: float, max_fraction: float = 0.10, daily_loss_pct: float = 0.0,
                 max_daily_loss_pct: float = 5.0) -> RiskDecision:
        reasons: list[str] = []
        if daily_loss_pct <= -abs(max_daily_loss_pct):
            reasons.append("daily_loss_limit")
        fraction = min(max(requested_fraction, 0.0), max_fraction)
        if fraction <= 0:
            reasons.append("zero_position")
        return RiskDecision(not reasons, fraction, reasons, {})


risk_manager = RiskManager()
