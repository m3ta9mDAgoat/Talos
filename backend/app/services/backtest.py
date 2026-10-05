from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from app.core.enums import Side, StopTrigger
from app.schemas.backtest import Candle, SignalBacktestResult, TargetHit
from app.schemas.signal import EntrySpec, NormalizedSignal


@dataclass(slots=True)
class _CloseStopEvent:
    timestamp: datetime
    close: float


def _seconds(timeframe: str) -> int:
    match = re.fullmatch(r"(\d+)([mhd])", timeframe.strip().lower())
    if not match:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    n = int(match.group(1))
    unit = match.group(2)
    return n * {"m": 60, "h": 3600, "d": 86400}[unit]


def _bucket_start(ts: datetime, timeframe: str) -> int:
    seconds = _seconds(timeframe)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    epoch = int(ts.timestamp())
    return epoch - (epoch % seconds)


def _candle_close_stop_events(candles: list[Candle], timeframe: str) -> dict[datetime, float]:
    """Return the last observed candle close for each aligned higher-timeframe bucket.

    This is correct when the input series is continuous and has a finer timeframe.
    Gaps are surfaced separately by the caller/market-data quality layer later.
    """
    last_by_bucket: dict[int, Candle] = {}
    for candle in candles:
        last_by_bucket[_bucket_start(candle.timestamp, timeframe)] = candle
    return {c.timestamp: c.close for c in last_by_bucket.values()}


def _entry_fill(entry: EntrySpec | None, candle: Candle, side: Side) -> float | None:
    if entry is None or entry.type == "market":
        return candle.open
    if entry.type == "price":
        assert entry.price is not None
        return entry.price if candle.low <= entry.price <= candle.high else None
    assert entry.min is not None and entry.max is not None
    low, high = entry.min, entry.max
    if low <= candle.open <= high:
        return candle.open
    if side == Side.BUY:
        if candle.open > high and candle.low <= high:
            return high
        if candle.open < low and candle.high >= low:
            return low
    else:
        if candle.open < low and candle.high >= low:
            return low
        if candle.open > high and candle.low <= high:
            return high
    return None


class SignalBacktester:
    """Small deterministic vertical-slice backtester for a single normalized signal.

    It deliberately records raw target/stop outcomes rather than inventing a money
    management policy. Portfolio P&L simulation is a separate layer in Talos.
    """

    def run(
        self,
        signal: NormalizedSignal,
        candles: list[Candle],
        *,
        candle_timeframe: str = "1m",
        conservative_intrabar: bool = True,
    ) -> SignalBacktestResult:
        candles = sorted((c for c in candles if c.timestamp >= signal.timestamp), key=lambda c: c.timestamp)
        warnings: list[str] = []
        target_hits = [TargetHit(target=t, hit=False) for t in signal.targets]
        if not candles:
            return SignalBacktestResult(
                symbol=signal.symbol,
                entry_triggered=False,
                targets=target_hits,
                warnings=["No candles exist at or after the signal timestamp."],
            )
        if signal.side not in {Side.BUY, Side.SELL}:
            return SignalBacktestResult(
                symbol=signal.symbol,
                entry_triggered=False,
                targets=target_hits,
                warnings=[f"Backtest does not execute side={signal.side}."],
            )

        entry_price: float | None = None
        entry_at: datetime | None = None
        entry_index = -1
        for i, candle in enumerate(candles):
            fill = _entry_fill(signal.entry, candle, signal.side)
            if fill is not None:
                entry_price, entry_at, entry_index = fill, candle.timestamp, i
                break

        if entry_price is None or entry_at is None:
            return SignalBacktestResult(
                symbol=signal.symbol,
                entry_triggered=False,
                targets=target_hits,
                warnings=warnings,
            )

        active_candles = candles[entry_index:]
        close_stop_events: dict[datetime, float] = {}
        if signal.stop and signal.stop.trigger == StopTrigger.CANDLE_CLOSE:
            assert signal.stop.timeframe
            try:
                if _seconds(candle_timeframe) > _seconds(signal.stop.timeframe):
                    warnings.append(
                        f"Input timeframe {candle_timeframe} is coarser than stop timeframe {signal.stop.timeframe}."
                    )
                close_stop_events = _candle_close_stop_events(active_candles, signal.stop.timeframe)
            except ValueError as exc:
                warnings.append(str(exc))

        max_price = entry_price
        min_price = entry_price
        stop_hit = False
        stop_at: datetime | None = None
        exit_price: float | None = None
        last_timestamp = entry_at

        for candle in active_candles:
            last_timestamp = candle.timestamp
            max_price = max(max_price, candle.high)
            min_price = min(min_price, candle.low)

            touch_stop = False
            if signal.stop and signal.stop.trigger == StopTrigger.PRICE_TOUCH:
                touch_stop = (
                    candle.low <= signal.stop.price
                    if signal.side == Side.BUY
                    else candle.high >= signal.stop.price
                )

            # Same-candle ordering is unknowable from OHLC alone. Conservative mode
            # assumes the adverse touch happened first.
            if touch_stop and conservative_intrabar:
                stop_hit, stop_at, exit_price = True, candle.timestamp, signal.stop.price
                break

            for target in target_hits:
                if target.hit:
                    continue
                reached = candle.high >= target.target if signal.side == Side.BUY else candle.low <= target.target
                if reached:
                    target.hit = True
                    target.hit_at = candle.timestamp

            if touch_stop:
                stop_hit, stop_at, exit_price = True, candle.timestamp, signal.stop.price
                break

            if signal.stop and signal.stop.trigger == StopTrigger.CANDLE_CLOSE:
                close = close_stop_events.get(candle.timestamp)
                if close is not None:
                    breached = close < signal.stop.price if signal.side == Side.BUY else close > signal.stop.price
                    if breached:
                        stop_hit, stop_at, exit_price = True, candle.timestamp, close
                        break

        if signal.side == Side.BUY:
            mfe_pct = (max_price - entry_price) / entry_price * 100
            mae_pct = (min_price - entry_price) / entry_price * 100
        else:
            mfe_pct = (entry_price - min_price) / entry_price * 100
            mae_pct = (entry_price - max_price) / entry_price * 100

        duration_minutes = (last_timestamp - entry_at).total_seconds() / 60
        return SignalBacktestResult(
            symbol=signal.symbol,
            entry_triggered=True,
            entry_price=entry_price,
            entry_at=entry_at,
            targets=target_hits,
            stop_hit=stop_hit,
            stop_at=stop_at,
            exit_price=exit_price,
            mfe_pct=round(mfe_pct, 6),
            mae_pct=round(mae_pct, 6),
            max_price=max_price,
            min_price=min_price,
            duration_minutes=duration_minutes,
            warnings=warnings,
        )


signal_backtester = SignalBacktester()
