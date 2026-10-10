"""Point-in-time OSINT aggregates for rule strategies and ML feature consumers.

Never use published_at as the availability clock. Only initial data observed by
Talos can be used until a later revision is separately time-stamped.
"""
from __future__ import annotations
from collections import deque
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.intelligence.classify import classify
from app.models.intelligence import IntelligenceEvent, IntelligenceRevision

OSINT_FEATURE_NAMES = frozenset({"osint_event_count", "osint_high_count", "osint_max_importance"})


def _aware(date):
    if date.tzinfo is None:
        return date.replace(tzinfo=timezone.utc)
    return date.astimezone(timezone.utc)


def _base_symbol(pair: str):
    token = pair.upper().split(":")[-1].replace("-", "/")
    if "/" in token:
        return token.split("/")[0]
    for quote in ("USDT", "USDC", "USD"):
        if token.endswith(quote) and len(token) > len(quote):
            return token[:-len(quote)]
    return token


def bar_osint_features(db: Session, candles, symbol: str, lookback_hours: int = 24) -> tuple[list[dict], str | None]:
    """One bounded SQL batch for all candle bars, not per-candle Neon polling.

    Returns per-bar feature dicts and warning when the archived timeline cannot
    support the beginning of the requested backtest. Unknown history returns
    None features (not zero, which would create a misleading trading backtest).
    """
    if not candles:
        return [], "No candle data"
    if not 1 <= lookback_hours <= 720:
        raise ValueError("OSINT lookback_hours must be 1..720")
    symbol = _base_symbol(symbol)
    start = _aware(candles[0].timestamp)
    end = _aware(candles[-1].timestamp)
    first = db.scalar(select(func.min(IntelligenceEvent.first_seen_at)))
    first = _aware(first) if first else None
    window = timedelta(hours=lookback_hours)
    if first is None:
        return [{k: None for k in OSINT_FEATURE_NAMES} for _ in candles], "No archived OSINT observations"
    # A feature at time T requires a lookback window covered by observed history.
    earliest_valid = first + window
    entries = db.execute(select(IntelligenceEvent.id, IntelligenceEvent.first_seen_at, IntelligenceEvent.category,
                                IntelligenceEvent.source_reliability).where(
        IntelligenceEvent.first_seen_at >= start - window,
        IntelligenceEvent.first_seen_at <= end,
    ).order_by(IntelligenceEvent.first_seen_at).limit(8000)).all()
    if not entries:
        return [{k: None if _aware(c.timestamp) < earliest_valid else 0 for k in OSINT_FEATURE_NAMES} for c in candles], (
            "OSINT archive begins after part of the requested timeframe" if start < earliest_valid else None)
    ids = [row.id for row in entries]
    originals = {r.event_id: r for r in db.scalars(select(IntelligenceRevision).where(
        IntelligenceRevision.event_id.in_(ids), IntelligenceRevision.revision == 1)).all()}
    series = []
    for row in entries:
        original = originals.get(row.id)
        if original is None:
            continue
        props = classify(original.title, original.summary, row.category, row.source_reliability)
        if symbol not in props["symbols"]:
            continue
        series.append((_aware(row.first_seen_at), props["importance"]))
    series.sort()
    values = []
    active = deque()
    cursor = 0
    for candle in candles:
        t = _aware(candle.timestamp)
        if t < earliest_valid:
            values.append({k: None for k in OSINT_FEATURE_NAMES})
            continue
        while cursor < len(series) and series[cursor][0] <= t:
            active.append(series[cursor])
            cursor += 1
        while active and active[0][0] < t - window:
            active.popleft()
        values.append({"osint_event_count": len(active),
                       "osint_high_count": sum(score >= 80 for _, score in active),
                       "osint_max_importance": max((score for _, score in active), default=0.0)})
    note = "OSINT archive does not cover the start of this backtest" if start < earliest_valid else None
    return values, note
