from __future__ import annotations

from statistics import median
from typing import Any

from app.core.enums import Side
from app.schemas.backtest import SignalBacktestResult
from app.schemas.signal import NormalizedSignal


def trade_return_pct(
    signal: NormalizedSignal,
    result: SignalBacktestResult,
    *,
    final_price: float | None,
    fee_bps: float,
    slippage_bps: float,
) -> float | None:
    if not result.entry_triggered or result.entry_price is None:
        return None
    entry = result.entry_price
    targets = signal.targets
    if not targets:
        return None

    n = len(targets)
    weight = 1.0 / n
    realized = 0.0
    hit_count = 0
    for hit in result.targets:
        if not hit.hit:
            continue
        hit_count += 1
        raw = (hit.target - entry) / entry if signal.side == Side.BUY else (entry - hit.target) / entry
        realized += weight * raw

    remaining = max(0.0, 1.0 - hit_count * weight)
    if remaining > 0:
        residual_exit = result.exit_price if result.stop_hit and result.exit_price is not None else final_price
        if residual_exit is not None:
            raw = (residual_exit - entry) / entry if signal.side == Side.BUY else (entry - residual_exit) / entry
            realized += remaining * raw

    round_trip_cost = 2.0 * (fee_bps + slippage_bps) / 10000.0
    return (realized - round_trip_cost) * 100.0


def summarize_returns(returns: list[float], portfolio_fraction: float) -> dict[str, Any]:
    if not returns:
        return {
            "trades": 0,
            "positive_trade_rate_pct": None,
            "average_return_pct": None,
            "median_return_pct": None,
            "profit_factor": None,
            "expectancy_pct": None,
            "model_equity_return_pct": None,
            "model_max_drawdown_pct": None,
        }

    wins = [x for x in returns if x > 0]
    losses = [x for x in returns if x < 0]
    profit_factor = None
    if losses:
        profit_factor = sum(wins) / abs(sum(losses)) if wins else 0.0
    elif wins:
        profit_factor = float("inf")

    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for ret in returns:
        equity *= 1.0 + portfolio_fraction * (ret / 100.0)
        peak = max(peak, equity)
        if peak > 0:
            max_dd = min(max_dd, (equity - peak) / peak)

    return {
        "trades": len(returns),
        "positive_trade_rate_pct": len(wins) / len(returns) * 100,
        "average_return_pct": sum(returns) / len(returns),
        "median_return_pct": median(returns),
        "profit_factor": None if profit_factor is None else ("inf" if profit_factor == float("inf") else profit_factor),
        "expectancy_pct": sum(returns) / len(returns),
        "model_equity_return_pct": (equity - 1.0) * 100,
        "model_max_drawdown_pct": max_dd * 100,
    }
