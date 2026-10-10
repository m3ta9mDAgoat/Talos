"""Auditable point-in-time NEWS research and paper-only automation.

Never equate a headline's importance/confidence with win probability. Real orders
are not automatically submitted by this module. Explicit human confirmation and
independent broker reconciliation are required for live execution.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.intelligence.classify import classify
from app.models.intelligence import IntelligenceEvent, IntelligenceRevision
from app.models.trading import NewsTradeDecisionRecord, PaperOrderRecord, PaperPositionRecord, ExecutionAuditRecord
from app.services.security import get_runtime, set_runtime

DEFAULT: dict[str, Any] = {
    "auto_paper": False,
    "auto_live": False,  # hard-disabled: never overridden by user config
    "broker_id": "binance", "quote_currency": "USDT",
    "quote_amount": 50.0, "max_position_fraction": 0.005,
    "min_importance": 75.0, "min_reliability": 0.8,
    "max_detect_lag_sec": 90, "max_news_age_sec": 90,
    "max_spread_bps": 15.0, "max_slippage_bps": 8.0,
    "fee_bps": 10.0, "stop_loss_pct": 1.0, "take_profit_pct": 1.8,
    "max_holding_minutes": 30, "max_daily_entries": 3,
    "cooldown_minutes": 30, "max_daily_loss_pct": 1.0, "max_news_exposure_fraction": 0.02,
}
CRYPTO = frozenset({"BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "BNB", "AVAX", "DOT", "LINK", "LTC"})
POSITIVE_TYPES = {"listing"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    return (dt.replace(tzinfo=timezone.utc) if dt and not dt.tzinfo else dt.astimezone(timezone.utc) if dt else None)


def config() -> dict[str, Any]:
    raw = get_runtime("news_trade_strategy", {})
    return {**DEFAULT, **{k: v for k, v in raw.items() if k in DEFAULT and k != "auto_live"}, "auto_live": False}


def update_config(changes: dict[str, Any]) -> dict[str, Any]:
    if changes.get("auto_live"):
        raise ValueError("Automatic real-money trading is unavailable: paper validation and independent live safety checks are required")
    settings = config()
    for key, value in changes.items():
        if key == "auto_live":
            # The UI sends the full displayed config, including the read-only false flag.
            continue
        if key not in DEFAULT:
            raise ValueError(f"Unsupported configuration: {key}")
        settings[key] = value
    limits = {"quote_amount": (5, 500), "max_position_fraction": (0.0001, .02),
              "min_importance": (60, 100), "min_reliability": (0.5, 1),
              "max_detect_lag_sec": (5, 600), "max_news_age_sec": (5, 600),
              "max_spread_bps": (0, 150), "max_slippage_bps": (0, 150),
              "fee_bps": (0, 100), "stop_loss_pct": (0.1, 10), "take_profit_pct": (0.1, 20),
              "max_holding_minutes": (1, 1440), "max_daily_entries": (1, 20),
              "cooldown_minutes": (0, 1440), "max_daily_loss_pct": (0.1, 10), "max_news_exposure_fraction": (0.001, 0.2)}
    for key, (low, high) in limits.items():
        value = settings[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not low <= float(value) <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
    if settings["broker_id"] not in {"binance", "okx", "bybit", "bitget", "kucoin", "mexc"}:
        raise ValueError("Unsupported broker")
    if settings["quote_currency"] != "USDT":
        raise ValueError("News strategy currently supports spot USDT only")
    if not isinstance(settings["auto_paper"], bool):
        raise ValueError("auto_paper must be a boolean")
    set_runtime("news_trade_strategy", settings)
    return settings


def classify_decision(event: IntelligenceEvent, first_revision: IntelligenceRevision | None, cfg: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    seen = aware(event.first_seen_at)
    published = aware(event.published_at)
    age = max(0, (now - seen).total_seconds()) if seen else float("inf")
    delay = max(0, (seen - published).total_seconds()) if published and seen else None
    # First recorded revision is the only material permitted for news-at-time inference.
    headline = first_revision.title if first_revision else event.headline
    snapshot = classify(headline, first_revision.summary if first_revision else event.summary, event.category, event.source_reliability)
    symbols = [x for x in snapshot["symbols"] if x in CRYPTO]
    eligible_direction = snapshot["event_type"] in POSITIVE_TYPES and bool(re.search(r"\b(?:listing|will list|lists|launches trading)\b", headline, re.I))
    reasons: list[str] = []
    if not first_revision:
        reasons.append("No original news revision archived: cannot prove point-in-time headline")
    if not symbols:
        reasons.append("No supported spot-crypto symbol identified; do not invent mappings for stocks/macroeconomic assets")
    if not eligible_direction:
        reasons.append("No explicitly bullish spot-listing rule; negative/ambiguous headlines are research-only")
    if float(event.importance) < cfg["min_importance"]:
        reasons.append("Importance is below configured news selection threshold")
    if float(event.source_reliability) < cfg["min_reliability"]:
        reasons.append("Source reliability below configured threshold")
    if published is None:
        reasons.append("Publisher timestamp unknown: autonomous entry cannot verify how stale news was")
    elif delay is not None and delay > cfg["max_detect_lag_sec"]:
        reasons.append("Already delayed from publication beyond freshness limit")
    if published and seen and published > seen + timedelta(minutes=2):
        reasons.append("Publisher timestamp ahead of observed time; check source clock")
    if age > cfg["max_news_age_sec"]:
        reasons.append("Missed freshness window; historical study only")
    return {"event_id": event.id, "source": event.source_key, "headline": headline,
            "event_type_at_first_observation": snapshot["event_type"], "first_seen_at": seen,
            "published_at": published, "detect_lag_sec": round(delay, 1) if delay is not None else None,
            "age_sec": round(age, 1), "importance": event.importance,
            "classification_confidence": snapshot["classification_confidence"],
            "source_reliability": event.source_reliability, "symbol": f"{symbols[0]}/USDT" if symbols else None,
            "side": "buy" if eligible_direction else None,
            "actionable": not reasons, "reasons": reasons,
            "estimated_win_probability_pct": None,
            "probability_note": "Uncalibrated; source ranking and classification scores are not trade win probabilities."}


def get_candidate(event_id: int, cfg: dict[str, Any] | None = None, now: datetime | None = None) -> dict[str, Any]:
    with SessionLocal() as db:
        event = db.get(IntelligenceEvent, event_id)
        if not event:
            raise ValueError("News event not found")
        first = db.scalar(select(IntelligenceRevision).where(IntelligenceRevision.event_id == event_id, IntelligenceRevision.revision == 1))
        return classify_decision(event, first, cfg or config(), now)


def historical_market_review(candles: list[dict[str, Any]], first_seen_ms: int, cfg: dict[str, Any]) -> dict[str, Any]:
    """Post-observation 1m OHLCV excursion, NOT an executable profit claim."""
    usable = sorted((c for c in candles if int(c["timestamp"]) >= first_seen_ms), key=lambda c: c["timestamp"])
    if len(usable) < 2:
        return {"available": False, "reason": "Insufficient 1-minute candles observed after Talos discovery"}
    origin = float(usable[0]["open"])
    if origin <= 0:
        return {"available": False, "reason": "Invalid bar opening price"}
    horizon = min(len(usable), int(cfg["max_holding_minutes"]))
    sampled = usable[:horizon]
    maximum = max(sampled, key=lambda c: float(c["high"]))
    minimum = min(float(x["low"]) for x in sampled)
    gross_peak = (float(maximum["high"]) / origin - 1) * 100
    costs_pct = (2 * float(cfg["fee_bps"]) + 2 * float(cfg["max_slippage_bps"])) / 100
    return {"available": True, "data_granularity": "1m OHLCV; no order-book or tick-level execution evidence",
            "baseline_open": origin, "peak_high": float(maximum["high"]),
            "max_favorable_excursion_pct": round(gross_peak, 4),
            "max_adverse_excursion_pct": round((minimum / origin - 1)*100, 4),
            "peak_less_assumed_costs_pct": round(gross_peak - costs_pct, 4),
            "assumed_round_trip_cost_pct": round(costs_pct, 4),
            "hypothetical_peak_at": datetime.fromtimestamp(int(maximum["timestamp"])/1000, tz=timezone.utc).isoformat(),
            "missed_opportunity_not_proven_fill": gross_peak - costs_pct > float(cfg["take_profit_pct"]),
            "warning": "A candle high is not an attainable fill, and the move cannot be attributed to this headline."}


async def preview(event_id: int, *, include_candles: bool = True) -> dict[str, Any]:
    cfg = config()
    result = get_candidate(event_id, cfg)
    result["settings"] = cfg
    result["market"] = None
    result["history"] = {"available": False, "reason": "No instrument matched"}
    result["market_error"] = None
    if not result["symbol"]:
        return result
    from app.services.brokers import ticker, ohlcv
    try:
        tick = await ticker(cfg["broker_id"], result["symbol"])
        quote_ts = tick.get("timestamp")
        quote_age = max(0, (utcnow().timestamp() * 1000 - int(quote_ts)) / 1000) if quote_ts else None
        spread = tick.get("spread_pct")
        spread_bps = float(spread) * 100 if spread is not None else None
        result["market"] = {"exchange": cfg["broker_id"], "last": tick.get("last"),
                            "bid": tick.get("bid"), "ask": tick.get("ask"),
                            "spread_bps": round(spread_bps, 3) if spread_bps is not None else None,
                            "quote_age_sec": round(quote_age, 2) if quote_age is not None else None,
                            "observed_at": datetime.fromtimestamp(quote_ts / 1000, tz=timezone.utc).isoformat() if quote_ts else None}
        if spread_bps is None or spread_bps > cfg["max_spread_bps"]:
            result["reasons"].append("Live bid/ask spread unavailable or exceeds limit")
        if quote_age is None or quote_age > 20:
            result["reasons"].append("Exchange quote timestamp missing/stale; cannot safely auto-enter")
        if not tick.get("ask") or not tick.get("bid") or not tick.get("last"):
            result["reasons"].append("Market ticker lacks executable bid/ask quote")
        if include_candles:
            seen = aware(result["first_seen_at"])
            since = int(seen.timestamp()*1000) - 120_000
            candles = await ohlcv(cfg["broker_id"], result["symbol"], "1m", min(120, int(cfg["max_holding_minutes"]) + 5), since)
            result["history"] = historical_market_review(candles.get("candles") or [], int(seen.timestamp()*1000), cfg)
            result["candles"] = (candles.get("candles") or [])[-90:]
    except Exception as exc:
        result["market_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        result["reasons"].append("Market feed unavailable; no trade permitted")
    result["actionable"] = not result["reasons"]
    return result


def _risk_guard(db, cfg, event_id: int, symbol: str, *, now: datetime) -> str | None:
    from app.models.trading import PaperPositionRecord, PaperAccountRecord
    account = db.scalar(select(PaperAccountRecord).where(PaperAccountRecord.name == "Default Paper"))
    if account is None:
        # Lazily create the sandbox account via the existing paper service.
        from app.services.paper_trading import ensure_default_account
        account = ensure_default_account(db)
    if float(cfg["quote_amount"]) > float(account.equity) * float(cfg["max_position_fraction"]):
        return "Size exceeds max position fraction of paper equity"
    if float(cfg["quote_amount"]) * (1 + float(cfg["fee_bps"])/10000) > float(account.cash_balance):
        return "Insufficient paper cash"
    open_news_positions = [p for p in db.scalars(select(PaperPositionRecord).where(
        PaperPositionRecord.status == "open")).all() if (p.metadata_json or {}).get("news_decision_id")]
    current_exposure = sum(float(p.entry_price) * float(p.quantity) for p in open_news_positions)
    if current_exposure + float(cfg["quote_amount"]) > float(account.equity) * float(cfg["max_news_exposure_fraction"]):
        return "Total open news exposure would exceed maximum portfolio fraction"
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = db.scalar(select(func.count(NewsTradeDecisionRecord.id)).where(
        NewsTradeDecisionRecord.mode == "paper", NewsTradeDecisionRecord.status == "executed",
        NewsTradeDecisionRecord.created_at >= start)) or 0
    if today >= cfg["max_daily_entries"]:
        return "Daily news-trade limit reached"
    closed_today = [p for p in db.scalars(select(PaperPositionRecord).where(
        PaperPositionRecord.status == "closed", PaperPositionRecord.closed_at >= start)).all()
        if (p.metadata_json or {}).get("news_decision_id")]
    daily_net = sum(float(p.realized_pnl or 0) - float((p.metadata_json or {}).get("news_entry_fee") or 0) for p in closed_today)
    if daily_net <= -float(account.equity) * float(cfg["max_daily_loss_pct"]) / 100:
        return "Daily realized news-trading loss limit reached"
    recent = db.scalar(select(NewsTradeDecisionRecord.id).where(
        NewsTradeDecisionRecord.mode == "paper", NewsTradeDecisionRecord.status == "executed",
        NewsTradeDecisionRecord.symbol == symbol,
        NewsTradeDecisionRecord.created_at >= now - timedelta(minutes=cfg["cooldown_minutes"])).limit(1))
    if recent:
        return "Per-symbol news trade cooldown"
    if db.scalar(select(PaperPositionRecord.id).where(
        PaperPositionRecord.symbol == symbol, PaperPositionRecord.status == "open").limit(1)):
        return "Spot position in same symbol is already open"
    return None


async def paper_entry(event_id: int, *, origin: str = "manual") -> dict[str, Any]:
    if origin not in {"manual", "auto", "telegram"}:
        raise ValueError("Invalid order origin")
    # Exchange checks are done BEFORE reserving an idempotency key; never use an
    # implied price from stale RSS/XML or retrospective OHLCV for a current fill.
    pv = await preview(event_id, include_candles=False)
    if not pv["actionable"]:
        raise ValueError("Entry blocked: " + "; ".join(pv["reasons"]))
    cfg = pv["settings"]
    now = utcnow()
    with SessionLocal() as db:
        if db.scalar(select(NewsTradeDecisionRecord.id).where(
                NewsTradeDecisionRecord.event_id == event_id, NewsTradeDecisionRecord.mode == "paper").limit(1)):
            raise ValueError("This event was already submitted to paper trading")
        guard = _risk_guard(db, cfg, event_id, pv["symbol"], now=now)
        if guard:
            raise ValueError(guard)
        row = NewsTradeDecisionRecord(event_id=event_id, mode="paper", origin=origin,
                                      status="reserved", symbol=pv["symbol"], broker_id=cfg["broker_id"],
                                      config_json=cfg, evidence_json=json.loads(json.dumps({k: v for k, v in pv.items() if k != "candles"}, default=str)))
        db.add(row)
        try:
            db.commit(); db.refresh(row)
        except IntegrityError:
            db.rollback()
            raise ValueError("Duplicate news-trade request blocked")
        decision_id = row.id
    try:
        from app.models.trading import PaperPositionRecord
        from app.services.paper_trading import ensure_default_account, paper_buy
        with SessionLocal() as db:
            account = ensure_default_account(db)
            # Recheck risk after reservation, before payment and marking trade.
            guard = _risk_guard(db, cfg, event_id, pv["symbol"], now=utcnow())
            if guard:
                raise ValueError(guard)
            fill = await paper_buy(db, account, symbol=pv["symbol"], quote_amount=float(cfg["quote_amount"]),
                                   fee_bps=float(cfg["fee_bps"]), broker_id=cfg["broker_id"],
                                   slippage_bps=float(cfg["max_slippage_bps"]))
            pos = db.get(PaperPositionRecord, fill["position_id"])
            pos.stop_price = fill["fill_price"] * (1 - cfg["stop_loss_pct"] / 100)
            pos.targets_json = [fill["fill_price"] * (1 + cfg["take_profit_pct"] / 100)]
            pos.metadata_json = {**(pos.metadata_json or {}), "news_event_id": event_id, "news_decision_id": decision_id,
                                 "news_entry_fee": fill["fee"], "news_config": cfg, "news_opened_at": utcnow().isoformat()}
            db.commit()
        with SessionLocal() as db:
            row = db.get(NewsTradeDecisionRecord, decision_id)
            row.status = "executed"; row.execution_json = fill; row.updated_at = utcnow(); db.commit()
        try:
            from app.services.notifications import send_message
            await send_message(f"📰 TALOS NEWS PAPER BUY #{event_id}\n{pv['symbol']} · {fill['quantity']:.8g} @ {fill['fill_price']:.8g}\n"
                               f"Not live. Origin: {origin}. Risk: stop {cfg['stop_loss_pct']}%, target {cfg['take_profit_pct']}%")
        except Exception:
            pass
        return {"ok": True, "mode": "paper", "decision_id": decision_id, "event_id": event_id, "fill": fill}
    except Exception as exc:
        with SessionLocal() as db:
            row = db.get(NewsTradeDecisionRecord, decision_id)
            if row and row.status == "reserved":
                row.status = "error"; row.error = f"{type(exc).__name__}: {str(exc)[:240]}"; row.updated_at = utcnow(); db.commit()
        raise


def metrics() -> dict[str, Any]:
    with SessionLocal() as db:
        rows = db.scalars(select(NewsTradeDecisionRecord).order_by(NewsTradeDecisionRecord.id.desc()).limit(500)).all()
        ids = [x.id for x in rows if x.status == "executed"]
        positions = db.scalars(select(PaperPositionRecord).order_by(PaperPositionRecord.id.desc()).limit(1000)).all()
        linked = [p for p in positions if (p.metadata_json or {}).get("news_decision_id") in ids]
        closed = [p for p in linked if p.status == "closed"]
        net_pnl = [float(p.realized_pnl or 0) - float((p.metadata_json or {}).get("news_entry_fee") or 0) for p in closed]
        total = len(net_pnl)
        wins = sum(p > 0 for p in net_pnl)
        sample_ready = total >= 30
        live_orders = int(db.scalar(select(func.count(ExecutionAuditRecord.id)).where(
            ExecutionAuditRecord.mode == "live", ExecutionAuditRecord.action == "create_order",
            ExecutionAuditRecord.status == "success")) or 0)
        return {"paper": {"executed": len(ids), "open": len(linked)-total, "closed": total,
                           "realized_net_pnl": round(sum(net_pnl), 4),
                           "historical_win_rate_pct": round(wins / total * 100, 2) if sample_ready else None,
                           "win_rate_sample": total, "calibrated": sample_ready,
                           "note": "Win rate is historical only (30+ closed trades); not a future success probability."},
                "live": {"automated": False, "order_count": live_orders, "closed": None, "realized_net_pnl": None,
                         "note": "No reconciled live P&L in this module. Live actions remain manual in Live Execution."},
                "promotion": {"eligible": False, "status": "paper_validation_required",
                              "criteria": "At least 30 closed paper trades across 14 days, positive net return, drawdown and latency review, independent broker verification and manual approval.",
                              "reason": "No automatic promotion or autonomous real-money order route is installed."}}


def decisions(limit: int = 100) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.scalars(select(NewsTradeDecisionRecord).order_by(NewsTradeDecisionRecord.id.desc()).limit(limit)).all()
        return [{"id": x.id, "event_id": x.event_id, "origin": x.origin, "mode": x.mode,
                 "status": x.status, "symbol": x.symbol, "error": x.error,
                 "execution": x.execution_json, "created_at": x.created_at} for x in rows]


async def auto_paper_cycle(limit: int = 15) -> dict[str, Any]:
    cfg = config()
    if not cfg["auto_paper"]:
        return {"enabled": False, "considered": 0, "executed": 0}
    now = utcnow()
    with SessionLocal() as db:
        ids = db.scalars(select(IntelligenceEvent.id).where(
            IntelligenceEvent.first_seen_at >= now - timedelta(seconds=cfg["max_news_age_sec"])
        ).order_by(IntelligenceEvent.id.desc()).limit(limit)).all()
    results = {"enabled": True, "considered": len(ids), "executed": 0, "skipped": []}
    for event_id in ids:
        candidate = get_candidate(event_id, cfg)
        if not candidate["actionable"]:
            continue
        try:
            await paper_entry(event_id, origin="auto")
            results["executed"] += 1
        except Exception as exc:
            results["skipped"].append({"event_id": event_id, "reason": str(exc)[:160]})
    return results


async def paper_exit_cycle() -> dict[str, Any]:
    """Best-effort sandbox bracket exits; NOT exchange-hosted protective orders."""
    from app.services.brokers import ticker
    from app.services.paper_trading import ensure_default_account, close_position
    now = utcnow()
    with SessionLocal() as db:
        ids = [p.id for p in db.scalars(select(PaperPositionRecord).where(
            PaperPositionRecord.status == "open").order_by(PaperPositionRecord.id.desc()).limit(30)).all()
            if (p.metadata_json or {}).get("news_decision_id")]
    closed = 0; errors = []
    for position_id in ids:
        try:
            with SessionLocal() as db:
                p = db.get(PaperPositionRecord, position_id)
                if not p or p.status != "open":
                    continue
                cfg = (p.metadata_json or {}).get("news_config") or DEFAULT
                market = await ticker(p.broker_id or cfg["broker_id"], p.symbol)
                bid = market.get("bid")
                if not bid:
                    continue
                old = aware(p.opened_at) or now
                reason = ("stop" if p.stop_price and float(bid) <= p.stop_price else
                          "target" if p.targets_json and float(bid) >= p.targets_json[0] else
                          "time" if now - old >= timedelta(minutes=int(cfg["max_holding_minutes"])) else None)
                if not reason:
                    continue
                account = ensure_default_account(db)
                result = await close_position(db, account, p.id, fee_bps=float(cfg["fee_bps"]),
                                              slippage_bps=float(cfg["max_slippage_bps"]))
                p.metadata_json = {**(p.metadata_json or {}), "news_exit_reason": reason, "news_exit_time": now.isoformat()}
                db.commit()
            closed += 1
            try:
                from app.services.notifications import send_message
                await send_message(f"📉 TALOS NEWS PAPER EXIT · {reason.upper()}\n{p.symbol} · P&L before entry fee {result['realized_pnl']:.4g}\nSandbox only; not an exchange stop order")
            except Exception:
                pass
        except Exception as exc:
            errors.append({"position_id": position_id, "error": str(exc)[:150]})
    return {"open_reviewed": len(ids), "closed": closed, "errors": errors}


async def paper_monitor_loop() -> None:
    import asyncio
    await asyncio.sleep(60)
    while True:
        try:
            await paper_exit_cycle()
        except Exception as exc:
            print(f"[news-paper-monitor] {type(exc).__name__}: {str(exc)[:140]}", flush=True)
        await asyncio.sleep(60)

# ─────────────────────────────────────────────────────────────────────
# Versioned news-sentiment research and late-news counterfactual replay.
# Read-only public OHLCV. No broker orders, paper balance movements or live orders.
# ─────────────────────────────────────────────────────────────────────
import asyncio
import hashlib
from app.models.trading import NewsSentimentReplayRun, NewsSentimentReplayRecord, NewsMissedAlertRecord

SENTIMENT_VERSION = "sentiment-rules-1.0.0"
SENTIMENT_DEFAULT = {
    "broker_id": "binance", "quote_amount": 50.0, "min_importance": 75.0,
    "min_reliability": 0.8, "min_sentiment_score": 3.0,
    "assumed_latency_sec": 5,  # hypothetical publisher availability -> request, NOT measured
    "max_realtime_detection_sec": 90,  # older => genuinely missed for live selection
    "fee_bps": 10.0, "slippage_bps": 8.0,
    "stop_loss_pct": 1.0, "take_profit_pct": 1.8,
    "max_holding_minutes": 30, "max_daily_entries": 3,
    "cooldown_minutes": 30, "max_position_fraction": 0.005,
    "max_exposure_fraction": 0.02, "starting_equity": 10000.0,
    "max_daily_loss_pct": 1.0,
}


def replay_settings(overrides: dict | None = None) -> dict:
    """Strict immutable snapshot; changes never silently alter a past run."""
    cfg = {**SENTIMENT_DEFAULT}
    overrides = overrides or {}
    if set(overrides) - set(SENTIMENT_DEFAULT):
        raise ValueError("Unrecognized sentiment replay setting")
    cfg.update(overrides)
    for k, a, b in [
        ("quote_amount", 5, 1000), ("min_importance", 0, 100),
        ("min_reliability", 0, 1), ("min_sentiment_score", 0.5, 8),
        ("assumed_latency_sec", 1, 120), ("max_realtime_detection_sec", 5, 1800), ("fee_bps", 0, 100),
        ("slippage_bps", 0, 150), ("stop_loss_pct", 0.1, 20),
        ("take_profit_pct", 0.1, 30), ("max_holding_minutes", 1, 120),
        ("max_daily_entries", 1, 30), ("cooldown_minutes", 0, 1440),
        ("max_position_fraction", 0.0001, .2), ("max_exposure_fraction", .001, .5),
        ("starting_equity", 100, 1_000_000), ("max_daily_loss_pct", 0.1, 10),
    ]:
        v = cfg[k]
        if isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or not a <= v <= b:
            raise ValueError(f"Invalid {k}; expected {a} to {b}")
    if cfg["broker_id"] not in {"binance", "okx", "bybit", "bitget", "kucoin", "mexc"}:
        raise ValueError("Unsupported OHLCV market")
    if cfg["quote_amount"] > cfg["starting_equity"] * cfg["max_position_fraction"]:
        raise ValueError("Trade size exceeds hypothetical starting equity risk limit")
    return cfg


def replay_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps({"version": SENTIMENT_VERSION, "config": cfg}, sort_keys=True).encode()).hexdigest()


SENTIMENT_PATTERNS = (
    ("spot_listing", r"\b(?:will list|new (?:spot )?listing|spot listing|lists?\s+\$?[a-z0-9]+|opens? spot trading)\b", 4),
    ("regulatory_approval", r"\b(?:sec approves?|regulator approves?|regulatory approval granted|etf approved)\b", 3),
    ("major_integration", r"\b(?:mainnet launch(?:ed)?|integration (?:live|completed))\b", 1.5),
    ("security_exploit", r"\b(?:hacked|exploit(?:ed)?|stolen funds|ransomware|security breach)\b", -5),
    ("delisting", r"\b(?:delist(?:ed|ing)?|trading pairs? remov(?:ed|al))\b", -5),
    ("halt", r"\b(?:trading suspended|withdrawals? halted|bankruptcy|insolvency)\b", -4),
    ("legal_adverse", r"\b(?:lawsuit|indictment|enforcement action|criminal charges)\b", -3),
    ("bullish_hint", r"\b(?:surges?|bullish|record high)\b", .5),
    ("bearish_hint", r"\b(?:plunges?|bearish|record low)\b", -.5),
    ("negated_unconfirmed", r"\b(?:rumou?r|unconfirmed|denies?|false report|not approved|not (?:yet )?listed|not\s+(?:going\s+to\s+)?list)\b", -6),
)

def sentiment_rules(title: str, summary: str, category: str, reliability: float) -> dict:
    """Human-auditable feature trace based only on first archived revision.

    Never use subsequent price movement to choose sentiment or set thresholds.
    Bears are logged as negative research signals; spot-only engine never shorts.
    """
    title = (title or "")[:750]
    summary = (summary or "")[:300]
    text = f"{title} {summary}".lower()
    base = classify(title, summary, category, reliability)
    trace = []
    score = 0.0
    for name, pattern, weight in SENTIMENT_PATTERNS:
        if re.search(pattern, text, re.I):
            score += weight
            trace.append({"rule": name, "weight": weight})
    # Futures-only products are not a spot buy. Reject uncertain headlines regardless of score.
    derivatives_only = bool(re.search(r"\b(?:futures?|perpetuals?|pre-market|margin)\b", text, re.I)) and not bool(re.search(r"\bspot\b", text, re.I))
    if derivatives_only:
        trace.append({"rule": "non_spot_derivative_veto", "weight": -6})
        score -= 6
    veto = any(x["rule"] in {"negated_unconfirmed", "non_spot_derivative_veto"} for x in trace)
    sentiment = "positive" if score >= 2 and not veto else "negative" if score <= -2 else "neutral"
    # Support new tokens only when exact parenthesized tickers/cashtags accompany explicit spot listing.
    symbols = [x for x in base["symbols"] if x in CRYPTO and x != "USDT"]
    if any(x["rule"] == "spot_listing" for x in trace):
        for token in re.findall(r"\(([A-Z0-9]{2,8})\)|\$([A-Z0-9]{2,8})\b", title):
            candidate = next((x for x in token if x), "")
            if candidate not in {"USDT", "USD", "SPOT", "TOKEN", "COIN", "LISTING", "BUSD"} and candidate not in symbols:
                symbols.append(candidate)
    return {"strategy_version": SENTIMENT_VERSION, "score": round(score, 3),
            "sentiment": sentiment, "features": trace, "importance": base["importance"],
            "classification": base["event_type"], "symbols": symbols,
            "veto": veto, "first_revision_only": True, "uses_future_prices": False}


def select_hypothetical(event: IntelligenceEvent, revision: IntelligenceRevision | None, cfg: dict) -> dict:
    reasons = []
    if not revision or revision.revision != 1:
        reasons.append("Original revision missing")
    if not event.published_at:
        reasons.append("Publisher timestamp missing")
    if event.published_at and aware(event.published_at) > utcnow() + timedelta(minutes=2):
        reasons.append("Publisher timestamp in future")
    if event.published_at and event.first_seen_at and aware(event.published_at) > aware(event.first_seen_at) + timedelta(minutes=2):
        reasons.append("Publisher timestamp inconsistent with first observation")
    features = sentiment_rules(revision.title, revision.summary, event.category, event.source_reliability) if revision else None
    if features:
        if features["sentiment"] != "positive" or features["score"] < cfg["min_sentiment_score"]:
            reasons.append("No unambiguous positive long-spot sentiment threshold")
        if not any(x["rule"] in {"spot_listing", "regulatory_approval"} for x in features["features"]):
            reasons.append("No whitelisted positive event rule")
        if not features["symbols"] or len(features["symbols"]) > 1:
            reasons.append("No single unambiguous crypto instrument")
        if features["importance"] < cfg["min_importance"]:
            reasons.append("Point-in-time editorial importance below threshold")
    if float(event.source_reliability) < cfg["min_reliability"]:
        reasons.append("Original source reliability below threshold")
    return {"selected": not reasons, "reasons": reasons, "features": features,
            "symbol": f"{features['symbols'][0]}/USDT" if features and len(features["symbols"]) == 1 else None,
            "assumptions": ["Counterfactual: original headline presumed readable by a fast news feed at publisher timestamp",
                            "Historical spread, depth, order-book impact and real fillability are UNKNOWN",
                            "Sentiment is frozen before reading historical price data"]}


def simulate_1m_ohlcv(bars: list[dict], published_at: datetime, cfg: dict, *, now: datetime | None = None) -> dict:
    """Conservative OHLC backtest. Entry at first candle OPEN >= publish+latency.

    Stops always take precedence if both bracket levels were touched in one bar.
    Partial bars, missing minute bars, unverified market fills are never labelled wins.
    """
    publication = aware(published_at)
    target = publication + timedelta(seconds=int(cfg["assumed_latency_sec"]))
    horizon = target + timedelta(minutes=int(cfg["max_holding_minutes"]))
    now = aware(now or utcnow())
    if horizon + timedelta(minutes=1) > now:
        return {"status": "unverifiable", "reason": "Holding horizon not yet complete"}
    ordered = sorted([b for b in bars if b.get("timestamp") is not None], key=lambda b: int(b["timestamp"]))
    start_ms = int(target.timestamp() * 1000)
    finish_ms = int(horizon.timestamp() * 1000)
    # Only fully elapsed 1-minute bars whose CLOSE is no later than the holding horizon.
    usable = [b for b in ordered if int(b["timestamp"]) >= start_ms and int(b["timestamp"]) + 60_000 <= finish_ms]
    if len(usable) < 2:
        return {"status": "unverifiable", "reason": "Missing historical bars after assumed execution"}
    first_ts = int(usable[0]["timestamp"])
    if first_ts - start_ms >= 60_000:
        return {"status": "unverifiable", "reason": "No 1m opening proxy within 60s of target"}
    if any(int(y["timestamp"]) - int(x["timestamp"]) != 60_000 for x, y in zip(usable, usable[1:])):
        return {"status": "unverifiable", "reason": "Gaps in historical OHLCV"}
    if int(usable[-1]["timestamp"]) < finish_ms - 120_000:
        return {"status": "unverifiable", "reason": "Insufficient bars to test complete holding period"}
    try:
        raw_entry = float(usable[0]["open"])
        if raw_entry <= 0 or not math.isfinite(raw_entry): raise ValueError()
        entry = raw_entry * (1 + float(cfg["slippage_bps"]) / 10000)
        stop = entry * (1 - float(cfg["stop_loss_pct"]) / 100)
        target_px = entry * (1 + float(cfg["take_profit_pct"]) / 100)
        # Entry happens at OPEN, so first-bar low/high are eligible after that open.
        # If target and stop hit in the same bar, always choose the adverse stop.
        exit_bar, raw_exit, reason = None, None, None
        for bar in usable:
            op, low, high = [float(bar[k]) for k in ("open", "low", "high")]
            if not (0 < low <= op <= high and all(math.isfinite(x) for x in (op, low, high))):
                return {"status": "unverifiable", "reason": "Invalid historical OHLCV"}
            if op <= stop or low <= stop:
                exit_bar, raw_exit, reason = bar, min(op, stop), "stop"  # adverse gaps respected
                break
            if high >= target_px:
                exit_bar, raw_exit, reason = bar, target_px, "target"  # no perfect high fills
                break
        if exit_bar is None:
            exit_bar, raw_exit, reason = usable[-1], float(usable[-1]["close"]), "time"
        exit_fill = raw_exit * (1 - float(cfg["slippage_bps"]) / 10000)
        cash = float(cfg["quote_amount"])
        qty = cash / entry
        fee_rate = float(cfg["fee_bps"]) / 10000
        pnl = qty * (exit_fill - entry) - cash * fee_rate - qty * exit_fill * fee_rate
        pct = pnl / cash * 100
        exit_ts = int(exit_bar["timestamp"]) + (60_000 if reason == "time" else 0)
        return {"status": "simulated", "entry_time": datetime.fromtimestamp(first_ts / 1000, tz=timezone.utc),
                "assumed_time": target, "exit_time": datetime.fromtimestamp(exit_ts / 1000, tz=timezone.utc),
                "proxy_delay_seconds": round((first_ts-start_ms)/1000, 3),
                "price_entry": round(entry, 12), "price_exit": round(exit_fill, 12),
                "net_pnl_quote": round(pnl, 8), "net_return_pct": round(pct, 6),
                "exit_reason": reason, "label": "صفقة ناجحة فائتة (محاكاة)" if pnl > 0 else "خاسرة فائتة (محاكاة)" if pnl < 0 else "محايدة فائتة (محاكاة)",
                "conservative_stop_priority": True, "bar_resolution": "1m", "claim_executable": False,
                "lookahead_in_selection": False,
                "assumptions": ["Entry: next available full 1-minute opening price after publish+assumed latency",
                                 "This opening-price proxy adds 0-60s and cannot validate seconds-level execution",
                                 "Stop wins ties within OHLC bar; gap-down exits at opening",
                                 "Fees and fixed adverse slippage included on both sides; historical spread/orderbook unknown"]}
    except (KeyError, TypeError, ValueError, OverflowError):
        return {"status": "unverifiable", "reason": "Non-numeric or malformed OHLCV"}


def enqueue_sentiment_replay(*, kind: str, days: int = 30, overrides: dict | None = None,
                             limit: int = 2000, start: datetime | None = None, end: datetime | None = None) -> dict:
    if kind not in {"auto", "manual"}: raise ValueError("Unrecognized run kind")
    if not 1 <= int(days) <= 90 or not 1 <= int(limit) <= 3000: raise ValueError("Invalid lookback/limit")
    cfg = replay_settings(overrides)
    finish = aware(end) or (utcnow()-timedelta(minutes=max(45, int(cfg["max_holding_minutes"])+2)))
    begin = aware(start) or finish - timedelta(days=days)
    if begin >= finish or finish > utcnow() - timedelta(minutes=int(cfg["max_holding_minutes"])+1):
        raise ValueError("Historical window must finish at least 31 minutes ago")
    with SessionLocal() as db:
        active = db.scalar(select(NewsSentimentReplayRun).where(NewsSentimentReplayRun.status.in_(["queued", "running"]))
                           .order_by(NewsSentimentReplayRun.id).limit(1))
        if active:
            if kind == "auto": return {"id": active.id, "status": active.status, "already_active": True}
            raise ValueError("A replay is already running; please wait")
        stmt = select(IntelligenceEvent.id).where(IntelligenceEvent.first_seen_at >= begin,
                                                   IntelligenceEvent.first_seen_at < finish).order_by(
            func.coalesce(IntelligenceEvent.published_at, IntelligenceEvent.first_seen_at), IntelligenceEvent.id
        ).limit(limit + 1)
        ordered_ids = list(db.scalars(stmt).all())
        total = len(ordered_ids)
        if total > limit:
            raise ValueError(f"Window has more than {limit} events. Reduce days or use multiple windows")
        # Freeze exact dataset membership AND sort chronologically by publisher time.
        # Later-arriving archive entries cannot alter this run's candidate denominator.
        stored_cfg = {**cfg, "_event_ids": ordered_ids}
        row = NewsSentimentReplayRun(kind=kind, status="queued", strategy_version=SENTIMENT_VERSION,
                                     strategy_hash=replay_hash(cfg), config_json=stored_cfg,
                                     window_start=begin, window_end=finish, total_events=total)
        db.add(row);db.commit();db.refresh(row)
        return {"id": row.id, "status": row.status, "kind": kind, "total_events": total,
                "strategy_version": SENTIMENT_VERSION, "hash": row.strategy_hash}


def replay_runs(limit=20) -> list[dict]:
    with SessionLocal() as db:
        rows = db.scalars(select(NewsSentimentReplayRun).order_by(NewsSentimentReplayRun.id.desc()).limit(limit)).all()
        return [{"id": r.id, "kind": r.kind, "status": r.status, "strategy_version": r.strategy_version,
                 "strategy_hash": r.strategy_hash, "processed": r.processed, "total_events": r.total_events,
                 "eligible": r.eligible, "progress_pct": round(r.processed / r.total_events * 100, 1) if r.total_events else 100,
                 "error": r.error, "created_at": r.created_at, "completed_at": r.completed_at,
                 "window_start": r.window_start, "window_end": r.window_end} for r in rows]


async def replay_one(run_id: int, event_id: int) -> dict:
    with SessionLocal() as db:
        run = db.get(NewsSentimentReplayRun, run_id)
        event = db.get(IntelligenceEvent, event_id)
        original = db.scalar(select(IntelligenceRevision).where(IntelligenceRevision.event_id == event_id,
                                  IntelligenceRevision.revision == 1))
        if not run or not event: raise ValueError("Missing run or event")
        cfg = run.config_json
        choice = select_hypothetical(event, original, cfg)
        data = {"strategy_hash": run.strategy_hash, "strategy_version": run.strategy_version,
                "rules": choice["features"], "reasons": choice["reasons"], "assumptions": choice["assumptions"],
                "source_key": event.source_key, "original_revision_hash": original.content_hash if original else None,
                "first_revision_observed_at": aware(original.observed_at).isoformat() if original else None,
                "published_at": aware(event.published_at).isoformat() if event.published_at else None,
                "first_seen_at": aware(event.first_seen_at).isoformat() if event.first_seen_at else None,
                "target_latency_sec": cfg["assumed_latency_sec"],
                "observed_detection_lag_sec": round((aware(event.first_seen_at)-aware(event.published_at)).total_seconds(),3) if event.published_at else None,
                "never_sent_to_exchange": True}
        pub = aware(event.published_at)
        seen = aware(event.first_seen_at)
        symbol = choice["symbol"]
    if not choice["selected"]:
        return {"event_id": event_id, "symbol": symbol, "sentiment": choice["features"]["sentiment"] if choice["features"] else "unknown",
                "score": choice["features"]["score"] if choice["features"] else 0, "selected": False,
                "status": "rejected", "label": "مرفوض بالقواعد", "published_at": pub, "first_seen_at": seen, "trace": data}
    from app.services.brokers import ohlcv
    target = pub + timedelta(seconds=int(cfg["assumed_latency_sec"]))
    begin_ms = int((target - timedelta(minutes=1)).timestamp()*1000)
    horizon = int(cfg["max_holding_minutes"])
    try:
        historical = await ohlcv(cfg["broker_id"], symbol, "1m", min(200, horizon + 8), begin_ms)
        sim = simulate_1m_ohlcv(historical.get("candles") or [], pub, cfg)
    except Exception as exc:
        sim = {"status": "unverifiable", "reason": f"Historical market unavailable: {type(exc).__name__}: {str(exc)[:160]}"}
    # Eligibility is based on actual observed delay, not just target+5-second mismatch.
    observed_lag = (seen-pub).total_seconds() if seen and pub else None
    missed_realtime_window = observed_lag is not None and observed_lag > cfg["max_realtime_detection_sec"]
    data["missed_realtime_window"] = missed_realtime_window
    data["historical_replay"] = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in sim.items()}
    if sim["status"] == "simulated":
        outcome = "ناجحة" if sim["net_pnl_quote"] > 0 else "خاسرة" if sim["net_pnl_quote"] < 0 else "محايدة"
        sim["label"] = f"صفقة {outcome} فائتة (محاكاة)" if missed_realtime_window else f"صفقة افتراضية {outcome} (محاكاة)"
    return {"event_id": event_id, "symbol": symbol, "sentiment": choice["features"]["sentiment"],
            "score": choice["features"]["score"], "selected": True,
            "status": sim["status"], "label": sim.get("label", "بيانات غير كافية"),
            "published_at": pub, "first_seen_at": seen,
            "assumed_entry_at": target,
            "actual_proxy_entry_at": sim.get("entry_time"), "exited_at": sim.get("exit_time"),
            "net_return_pct": sim.get("net_return_pct"), "net_pnl_quote": sim.get("net_pnl_quote"), "trace": data}


def _portfolio_guard(run_id: int, result: dict, cfg: dict) -> str | None:
    if result["status"] != "simulated": return None
    entry_time = result["actual_proxy_entry_at"]
    cash = cfg["quote_amount"]
    with SessionLocal() as db:
        prior = db.scalars(select(NewsSentimentReplayRecord).where(
            NewsSentimentReplayRecord.run_id == run_id,
            NewsSentimentReplayRecord.status == "simulated")).all()
    same_day = [x for x in prior if x.actual_proxy_entry_at and aware(x.actual_proxy_entry_at).date() == entry_time.date() and aware(x.actual_proxy_entry_at) <= entry_time]
    if len(same_day) >= cfg["max_daily_entries"]: return "Daily backtest entry cap"
    for x in prior:
        if x.symbol == result["symbol"] and x.actual_proxy_entry_at and \
           timedelta(0) <= entry_time - aware(x.actual_proxy_entry_at) < timedelta(minutes=cfg["cooldown_minutes"]):
            return "Per-symbol cooldown in chronological replay"
    open_at_entry = [x for x in prior if x.actual_proxy_entry_at and x.exited_at and
                     aware(x.actual_proxy_entry_at) <= entry_time < aware(x.exited_at)]
    if (len(open_at_entry)+1)*cash > cfg["starting_equity"]*cfg["max_exposure_fraction"]:
        return "Historical portfolio exposure limit"
    # Daily loss gate only considers outcomes already completed at the proposed entry time.
    realized = sum(x.net_pnl_quote or 0 for x in prior if x.exited_at and
                   aware(x.exited_at).date() == entry_time.date() and aware(x.exited_at) <= entry_time)
    if realized <= -cfg["starting_equity"] * cfg["max_daily_loss_pct"] / 100:
        return "Hypothetical daily loss gate"
    return None


async def process_replay_batch(batch: int = 5) -> dict:
    with SessionLocal() as db:
        run = db.scalar(select(NewsSentimentReplayRun).where(NewsSentimentReplayRun.status.in_(["queued", "running"]))
                        .order_by(NewsSentimentReplayRun.id).limit(1))
        if not run: return {"active": False}
        run.status = "running"; run.updated_at = utcnow(); db.commit()
        run_id, cfg = run.id, run.config_json
        events = cfg.get("_event_ids", [])[run.processed:run.processed+batch]
    if not events:
        with SessionLocal() as db:
            run = db.get(NewsSentimentReplayRun, run_id)
            run.status = "completed"; run.completed_at = utcnow(); run.updated_at = utcnow(); db.commit()
        return {"active": False, "completed": run_id}
    for event_id in events:
        try:
            result = await replay_one(run_id, event_id)
            if result["selected"] and result["status"] == "simulated":
                refused = _portfolio_guard(run_id, result, cfg)
                if refused:
                    result["trace"]["portfolio_guard_reason"] = refused
                    result.update({"status": "rejected", "selected": False, "label": "رفضته إدارة المخاطر",
                                   "net_pnl_quote": None, "net_return_pct": None})
            with SessionLocal() as db:
                run = db.get(NewsSentimentReplayRun, run_id)
                trace = result.pop("trace")
                row = NewsSentimentReplayRecord(run_id=run_id, strategy_version=SENTIMENT_VERSION,
                        trace_json=trace, **result)
                db.add(row)
                # The outbox and the replay result commit atomically. Manual / historical
                # replay runs produce records only and never spam the notification bot.
                if (run.kind == "auto" and result["selected"] and
                    result["status"] == "simulated" and trace.get("missed_realtime_window") and
                    result.get("net_pnl_quote") is not None and result["net_pnl_quote"] != 0):
                    payload = {
                        "event_id": event_id, "run_id": run_id, "symbol": result["symbol"],
                        "net_return_pct": float(result["net_return_pct"]),
                        "net_pnl_quote": float(result["net_pnl_quote"]),
                        "published_at": result["published_at"].isoformat() if result["published_at"] else None,
                        "first_seen_at": result["first_seen_at"].isoformat() if result["first_seen_at"] else None,
                        "assumed_entry_at": result["assumed_entry_at"].isoformat() if result["assumed_entry_at"] else None,
                        "actual_proxy_entry_at": result["actual_proxy_entry_at"].isoformat() if result["actual_proxy_entry_at"] else None,
                        "exited_at": result["exited_at"].isoformat() if result["exited_at"] else None,
                        "detect_lag_sec": trace.get("observed_detection_lag_sec"),
                        "latency_target_sec": trace.get("target_latency_sec"),
                        "exit_reason": trace.get("historical_replay", {}).get("exit_reason"),
                        "strategy_version": SENTIMENT_VERSION, "strategy_hash": run.strategy_hash,
                        "source_key": trace.get("source_key"),
                        "data_warning": "1m OHLCV proxy, NOT actual 5-second fill or realized PnL",
                    }
                    try:
                        with db.begin_nested():
                            db.add(NewsMissedAlertRecord(event_id=event_id, run_id=run_id,
                                result="win" if result["net_pnl_quote"] > 0 else "loss",
                                status="pending", payload_json=payload))
                            db.flush()
                    except IntegrityError:
                        # Another run already published this event; never duplicate it.
                        pass
                run = db.get(NewsSentimentReplayRun, run_id)
                run.last_event_id = event_id
                run.processed += 1
                if result["selected"]: run.eligible += 1
                run.updated_at = utcnow(); db.commit()
        except IntegrityError:
            with SessionLocal() as db:
                db.rollback()
                run = db.get(NewsSentimentReplayRun, run_id)
                run.last_event_id = event_id; run.processed += 1; db.commit()
        except Exception as exc:
            # Preserve an error record and advance. Unverifiable != losing trade.
            with SessionLocal() as db:
                run = db.get(NewsSentimentReplayRun, run_id)
                db.add(NewsSentimentReplayRecord(run_id=run_id, event_id=event_id,
                      strategy_version=SENTIMENT_VERSION, sentiment="unknown", score=0, selected=False,
                      status="unverifiable", label="خطأ في إعادة التمثيل", trace_json={"error": str(exc)[:250]}))
                run.last_event_id = event_id; run.processed += 1
                run.error = f"Some events could not be replayed: {type(exc).__name__}"; run.updated_at = utcnow()
                db.commit()
    return {"active": True, "run_id": run_id, "batch": len(events)}


async def sentiment_replay_loop():
    """Durable SQL cursor, resumes queued/running runs after backend restarts."""
    await asyncio.sleep(12)
    while True:
        try:
            with SessionLocal() as db:
                active = db.scalar(select(NewsSentimentReplayRun.id).where(
                    NewsSentimentReplayRun.status.in_(["queued", "running"])).limit(1))
                last_auto = db.scalar(select(NewsSentimentReplayRun).where(
                    NewsSentimentReplayRun.kind == "auto").order_by(NewsSentimentReplayRun.id.desc()).limit(1))
                start = aware(last_auto.window_end) if last_auto else utcnow()-timedelta(days=1)
            end = utcnow() - timedelta(minutes=45)
            if not active and end-start >= timedelta(minutes=15):
                try:
                    enqueue_sentiment_replay(kind="auto", limit=3000, start=start, end=end)
                except ValueError as exc:
                    print(f"[sentiment-replay] auto window blocked: {exc}", flush=True)
            state = await process_replay_batch(batch=5)
            await send_pending_missed_alerts(max_count=3)
            await asyncio.sleep(2 if state.get("active") else 60)
        except Exception as exc:
            print(f"[sentiment-replay] {type(exc).__name__}: {str(exc)[:240]}", flush=True)
            await asyncio.sleep(60)


def sentiment_records(*, run_id: int | None = None, days: int = 30, limit: int = 100) -> list[dict]:
    since = utcnow() - timedelta(days=days)
    with SessionLocal() as db:
        stmt = select(NewsSentimentReplayRecord)
        if run_id is not None:
            stmt = stmt.where(NewsSentimentReplayRecord.run_id == run_id)
        else:
            stmt = stmt.join(NewsSentimentReplayRun, NewsSentimentReplayRun.id == NewsSentimentReplayRecord.run_id).where(
                NewsSentimentReplayRun.kind == "auto", NewsSentimentReplayRecord.first_seen_at >= since)
        rows = db.scalars(stmt.order_by(NewsSentimentReplayRecord.id.desc()).limit(limit)).all()
        return [{"id": r.id, "run_id": r.run_id, "event_id": r.event_id,
                 "version": r.strategy_version, "symbol": r.symbol, "sentiment": r.sentiment,
                 "score": r.score, "selected": r.selected, "status": r.status, "label": r.label,
                 "published_at": r.published_at, "first_seen_at": r.first_seen_at,
                 "assumed_entry_at": r.assumed_entry_at, "actual_proxy_entry_at": r.actual_proxy_entry_at,
                 "exited_at": r.exited_at, "net_return_pct": r.net_return_pct,
                 "net_pnl_quote": r.net_pnl_quote, "trace": r.trace_json} for r in rows]


def sentiment_metrics(*, run_id: int | None = None, days: int = 30) -> dict:
    since = utcnow() - timedelta(days=days)
    with SessionLocal() as db:
        stmt = select(NewsSentimentReplayRecord)
        if run_id is not None:
            stmt = stmt.where(NewsSentimentReplayRecord.run_id == run_id)
        else:
            stmt = stmt.join(NewsSentimentReplayRun, NewsSentimentReplayRun.id == NewsSentimentReplayRecord.run_id).where(
                NewsSentimentReplayRun.kind == "auto", NewsSentimentReplayRecord.first_seen_at >= since)
        rows = db.scalars(stmt).all()
        selected = [x for x in rows if x.selected]
        evaluated = [x for x in selected if x.status == "simulated" and x.net_pnl_quote is not None]
        wins = [x for x in evaluated if x.net_pnl_quote > 0]
        losses = [x for x in evaluated if x.net_pnl_quote < 0]
        # Cash-equity proxy, sorted by realized EXIT time; not mark-to-market intrabar DD.
        ordered = sorted(evaluated, key=lambda x: aware(x.exited_at))
        total_cash = 10_000.0
        if run_id is not None:
            run = db.get(NewsSentimentReplayRun, run_id)
            if run: total_cash = float(run.config_json.get("starting_equity", total_cash))
        equity = total_cash; peak = equity; max_dd = 0.0
        for x in ordered:
            equity += float(x.net_pnl_quote)
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak * 100) if peak > 0 else max_dd
        gross_positive = sum(float(x.net_pnl_quote) for x in wins)
        gross_negative = -sum(float(x.net_pnl_quote) for x in losses)
        winning_eligible = sum(1 for x in wins if (x.trace_json or {}).get("missed_realtime_window"))
        trading_returns = sorted(float(x.net_return_pct) for x in evaluated)
        mean_return = sum(trading_returns)/len(trading_returns) if trading_returns else None
        median_return = (trading_returns[len(trading_returns)//2] if len(trading_returns)%2 else
                         (trading_returns[len(trading_returns)//2-1]+trading_returns[len(trading_returns)//2])/2) if trading_returns else None
        avwin = sum(float(x.net_pnl_quote) for x in wins)/len(wins) if wins else None
        avloss = sum(float(x.net_pnl_quote) for x in losses)/len(losses) if losses else None
        win_loss_ratio = avwin / abs(avloss) if avwin is not None and avloss and avloss != 0 else None
        max_loss_streak = 0; loss_streak = 0
        for x in ordered:
            loss_streak = loss_streak + 1 if float(x.net_pnl_quote) < 0 else 0
            max_loss_streak = max(max_loss_streak, loss_streak)
        delays = [float((aware(x.first_seen_at)-aware(x.published_at)).total_seconds()) for x in rows
                  if x.first_seen_at and x.published_at and 0 <= (aware(x.first_seen_at)-aware(x.published_at)).total_seconds()]
        proxy_lags = [float((aware(x.actual_proxy_entry_at)-aware(x.assumed_entry_at)).total_seconds()) for x in evaluated
                      if x.actual_proxy_entry_at and x.assumed_entry_at]
        statuses = {}
        for x in rows: statuses[x.status] = statuses.get(x.status, 0) + 1
        return {"scope": "manual_run" if run_id is not None else "automatic_last_n_days",
                "run_id": run_id, "lookback_days": days, "version": SENTIMENT_VERSION,
                "reviewed": len(rows), "sentiment_eligible": len(selected),
                "simulated": len(evaluated), "unverifiable": statuses.get("unverifiable", 0),
                "rejected": statuses.get("rejected", 0), "wins": len(wins), "losses": len(losses),
                "missed_profitable": winning_eligible,
                "historical_win_rate_pct": round(len(wins)/len(evaluated)*100, 3) if evaluated else None,
                "net_pnl_quote": round(sum(float(x.net_pnl_quote) for x in evaluated), 4),
                "sum_trade_returns_pct": round(sum(float(x.net_return_pct) for x in evaluated), 4),
                "gross_profit_quote": round(gross_positive, 4), "gross_loss_quote": round(gross_negative, 4),
                "profit_factor": round(gross_positive/gross_negative, 4) if gross_negative else None,
                "average_trade_return_pct": round(mean_return, 4) if mean_return is not None else None,
                "median_trade_return_pct": round(median_return, 4) if median_return is not None else None,
                "average_win_quote": round(avwin, 4) if avwin is not None else None,
                "average_loss_quote": round(avloss, 4) if avloss is not None else None,
                "payoff_ratio": round(win_loss_ratio, 4) if win_loss_ratio is not None else None,
                "maximum_consecutive_losses": max_loss_streak,
                "average_detect_lag_sec": round(sum(delays)/len(delays), 2) if delays else None,
                "average_additional_candle_delay_sec": round(sum(proxy_lags)/len(proxy_lags), 2) if proxy_lags else None,
                "cash_proxy_total_return_pct": round((equity/total_cash-1)*100, 4),
                "cash_proxy_max_drawdown_pct": round(max_dd, 4),
                "note": "Counterfactual minute-OHLC backtest, with publication-time access ASSUMED, not demonstrated. 1m entries lag target by up to 60s. No historical bid/ask, market impact, true fills or intrabar equity drawdown. Results are not realized/ paper/ live P&L."}


def missed_alert_message(payload: dict, result: str) -> str:
    """Telegram messages are explicitly counterfactual and distinguish 1m proxy fills."""
    def dt(name):
        return (payload.get(name) or "—").replace("T", " ")[:19] + (" UTC" if payload.get(name) else "")
    net = float(payload["net_pnl_quote"])
    ret = float(payload["net_return_pct"])
    lag = payload.get("detect_lag_sec")
    lag_label = f"{float(lag):.1f}s" if lag is not None else "unknown"
    title = "🟢 MISSED WIN (SIMULATION)" if result == "win" else "🔴 MISSED LOSS (SIMULATION)"
    return (
        f"{title}\n"
        f"TALOS | {payload.get('symbol') or 'unknown'} | Event #{payload['event_id']}\n"
        f"News published: {dt('published_at')}\n"
        f"Talos first saw: {dt('first_seen_at')} (late by {lag_label})\n"
        f"Target request latency: {payload.get('latency_target_sec')}s (assumed, NOT measured)\n"
        f"Target hypothetical entry: {dt('assumed_entry_at')}\n"
        f"1m candle proxy entry: {dt('actual_proxy_entry_at')}\n"
        f"Simulated exit: {dt('exited_at')} | {payload.get('exit_reason') or 'unknown'}\n"
        f"NET after assumed fees/slippage: {ret:+.3f}% | {net:+.4f} USDT\n"
        f"Rules: {payload.get('strategy_version')} | research only\n"
        "⚠️ Historical counterfactual. 1m bars cannot prove a 5-second fill or live profit. No real/paper order was submitted."
    )


async def send_pending_missed_alerts(*, max_count: int = 3) -> dict:
    """DB outbox retries Telegram outages; disabled toggles preserve pending results.

    A single worker claims each record with an expiring lease; sends are at-least-once.
    A rare process crash after Telegram accepts a message can duplicate its delivery.
    """
    from sqlalchemy import or_, update
    from app.services.notifications import notification_config, send_message
    cfg = notification_config()
    if not cfg.get("enabled", True):
        return {"sent": 0, "paused": "notifications disabled"}
    done = 0
    for _ in range(max_count):
        now = utcnow()
        with SessionLocal() as db:
            record = db.scalar(select(NewsMissedAlertRecord).where(
                or_(NewsMissedAlertRecord.status == "pending",
                    (NewsMissedAlertRecord.status == "sending") &
                    (NewsMissedAlertRecord.claimed_at < now - timedelta(minutes=2))),
                or_(NewsMissedAlertRecord.next_attempt_at.is_(None), NewsMissedAlertRecord.next_attempt_at <= now)
            ).order_by(NewsMissedAlertRecord.id).limit(1))
            if record is None:
                break
            # Skip disabled category without discarding the alert; continue on re-enable.
            gate = "missed_wins" if record.result == "win" else "missed_losses"
            if not cfg.get(gate, True):
                # Deliberately defer this record, allowing alerts of the other category.
                record.next_attempt_at = now + timedelta(minutes=5)
                db.commit()
                continue
            # Compare-and-swap permits safe concurrent workers with one SQL update.
            claim = db.execute(update(NewsMissedAlertRecord).where(
                NewsMissedAlertRecord.id == record.id,
                or_(NewsMissedAlertRecord.status == "pending",
                    (NewsMissedAlertRecord.status == "sending") &
                    (NewsMissedAlertRecord.claimed_at < now - timedelta(minutes=2)))
            ).values(status="sending", claimed_at=now, attempts=NewsMissedAlertRecord.attempts+1))
            if claim.rowcount != 1:
                db.rollback()
                continue
            message_id = record.id
            payload = dict(record.payload_json)
            result = record.result
            db.commit()
        try:
            response = await send_message(missed_alert_message(payload, result))
            if not response.get("ok"):
                # No configured chat / service disabled: keep pending, do not mark sent.
                raise RuntimeError(response.get("skipped", "Telegram did not accept message"))
            with SessionLocal() as db:
                item = db.get(NewsMissedAlertRecord, message_id)
                if item and item.status == "sending":
                    item.status = "sent"
                    item.sent_at = utcnow()
                    item.telegram_message_id = response.get("message_id")
                    item.last_error = None
                    db.commit()
                    done += 1
        except Exception as exc:
            with SessionLocal() as db:
                item = db.get(NewsMissedAlertRecord, message_id)
                if item and item.status == "sending":
                    item.status = "pending"
                    item.claimed_at = None
                    # Reduce polling and Telegram rate-limit pressure after failures.
                    retry_sec = min(3600, 30 * (2 ** min(item.attempts-1, 7)))
                    item.next_attempt_at = utcnow() + timedelta(seconds=retry_sec)
                    # Avoid exposing Telegram bot token through exception URLs in logs/DB.
                    item.last_error = f"{type(exc).__name__}: delivery pending (attempt {item.attempts})"
                    db.commit()
            print(f"[news-missed-alert] {type(exc).__name__}: alert #{message_id} queued for retry", flush=True)
            break
        await asyncio.sleep(1.1)  # Telegram private chat throughput protection
    return {"sent": done}


def missed_alert_delivery_stats() -> dict:
    with SessionLocal() as db:
        rows = db.execute(select(NewsMissedAlertRecord.status, NewsMissedAlertRecord.result,
                                 func.count(NewsMissedAlertRecord.id)).group_by(
                                 NewsMissedAlertRecord.status, NewsMissedAlertRecord.result)).all()
    return {"delivery": {f"{status}_{result}": int(count) for status, result, count in rows},
            "note": "At-least-once delivery; manual historical runs do not send individual alerts."}
