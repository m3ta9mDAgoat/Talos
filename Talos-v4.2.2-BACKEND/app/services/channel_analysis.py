from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.enums import MarketType, Side, SignalStatus
from app.db.session import SessionLocal
from app.models.analysis import AnalysisRunRecord, SignalBacktestRecord
from app.models.signal import SignalRecord
from app.models.source import SourceRecord
from app.schemas.analysis import AnalyzeSourceRequest
from app.schemas.signal import EntrySpec, NormalizedSignal, StopSpec
from app.services.backtest import signal_backtester
from app.services.market_data import market_data_resolver
from app.services.trade_metrics import summarize_returns, trade_return_pct


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _signal_from_record(row: SignalRecord) -> NormalizedSignal:
    entry = EntrySpec(**row.entry) if row.entry else None
    stop = StopSpec(**row.stop) if row.stop else None
    symbol_parts = row.symbol.split("/")
    return NormalizedSignal(
        strategy_id=row.strategy_id,
        strategy_version=row.strategy_version,
        source=row.source,
        source_id=row.source_id,
        source_message_id=row.source_message_id,
        timestamp=row.timestamp,
        market_type=MarketType(row.market_type),
        symbol=row.symbol,
        quote=symbol_parts[1] if len(symbol_parts) > 1 else None,
        side=Side(row.side),
        entry=entry,
        stop=stop,
        targets=list(row.targets or []),
        confidence=row.confidence,
        status=SignalStatus(row.status),
        raw_text=row.raw_text,
        metadata=row.metadata_json or {},
    )


def create_run(source_id: int, request: AnalyzeSourceRequest) -> AnalysisRunRecord:
    """Create a durable SQL-backed backtest run.

    V2.2 deliberately does *not* start an in-memory background asyncio task.
    Render free instances can sleep/restart, which made old runs disappear. The UI
    advances the run in small persisted batches via /advance. A restart therefore
    resumes from SignalBacktestRecord rows already stored in Postgres.
    """
    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            raise ValueError("Source not found")
        row = AnalysisRunRecord(
            source_id=source_id,
            status="queued",
            stage="queued",
            progress=0.0,
            config_json=request.model_dump(mode="json"),
            summary_json={"engine": "durable_chunked_v2.2"},
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def _eligibility(source_identifier: str, cfg: AnalyzeSourceRequest) -> dict[str, Any]:
    with SessionLocal() as db:
        all_rows = (
            db.query(SignalRecord)
            .filter(SignalRecord.source_id == source_identifier)
            .order_by(SignalRecord.timestamp.asc())
            .limit(cfg.max_signals)
            .all()
        )

    status_counts = Counter(str(r.status) for r in all_rows)
    side_counts = Counter(str(r.side) for r in all_rows)
    parsed_buy = [r for r in all_rows if str(r.status) == SignalStatus.PARSED.value and str(r.side) == Side.BUY.value]

    eligible: list[dict[str, Any]] = []
    excluded_edited = excluded_no_stop = excluded_missing_entry = excluded_missing_targets = 0
    edited_snapshot_count = 0
    for r in parsed_buy:
        metadata = dict(r.metadata_json or {})
        if metadata.get("edit_date"):
            edited_snapshot_count += 1
        signal = _signal_from_record(r)
        if cfg.exclude_edited and metadata.get("edit_date"):
            excluded_edited += 1
            continue
        if cfg.require_stop and signal.stop is None:
            excluded_no_stop += 1
            continue
        if signal.entry is None:
            excluded_missing_entry += 1
            continue
        if not signal.targets:
            excluded_missing_targets += 1
            continue
        eligible.append({"id": r.id, "signal": signal, "metadata": metadata})

    return {
        "all_rows": all_rows,
        "parsed_buy": parsed_buy,
        "eligible": eligible,
        "status_counts": status_counts,
        "side_counts": side_counts,
        "edited_snapshot_count": edited_snapshot_count,
        "excluded_edited": excluded_edited,
        "excluded_missing_stop": excluded_no_stop,
        "excluded_missing_entry": excluded_missing_entry,
        "excluded_missing_targets": excluded_missing_targets,
    }


async def _fetch_with_fallback(signal: NormalizedSignal, cfg: AnalyzeSourceRequest):
    end = signal.timestamp + timedelta(days=cfg.horizon_days)
    candidates = await market_data_resolver.resolve(signal.symbol, cfg.exchanges)
    errors: list[str] = []
    if not candidates:
        return None, [], ["Symbol is not currently resolvable on configured public CCXT spot venues."]
    for candidate in candidates:
        try:
            candles = await market_data_resolver.candles(
                exchange_id=candidate.exchange,
                symbol=candidate.symbol,
                timeframe=cfg.timeframe,
                start=signal.timestamp,
                end=end,
            )
        except Exception as exc:
            errors.append(f"{candidate.exchange}: {type(exc).__name__}: {exc}")
            continue
        if candles:
            return candidate, candles, errors
        errors.append(f"{candidate.exchange}: no candles returned for requested historical window")
    return None, [], errors


async def _analyze_one(item: dict[str, Any], cfg: AnalyzeSourceRequest, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    signal_id = int(item["id"])
    signal: NormalizedSignal = item["signal"]

    if signal.market_type != MarketType.SPOT or signal.side != Side.BUY:
        return {
            "signal_id": signal_id,
            "signal": signal,
            "record": {
                "status": "unsupported",
                "return_pct": None,
                "exchange": None,
                "market_symbol": None,
                "result_json": {"reason": "Current engine executes long spot BUY signals only."},
            },
        }

    async with semaphore:
        candidate, candles, fetch_errors = await _fetch_with_fallback(signal, cfg)

    if not candles or candidate is None:
        return {
            "signal_id": signal_id,
            "signal": signal,
            "record": {
                "status": "no_market_data",
                "return_pct": None,
                "exchange": None,
                "market_symbol": None,
                "result_json": {"errors": fetch_errors[-10:]},
            },
        }

    result = signal_backtester.run(
        signal,
        candles,
        candle_timeframe=cfg.timeframe,
        conservative_intrabar=cfg.conservative_intrabar,
    )
    ret = trade_return_pct(
        signal,
        result,
        final_price=candles[-1].close if candles else None,
        fee_bps=cfg.fee_bps,
        slippage_bps=cfg.slippage_bps,
    )
    payload = result.model_dump(mode="json")
    payload.update({
        "fetch_errors": fetch_errors[-10:],
        "timeframe": cfg.timeframe,
        "horizon_days": cfg.horizon_days,
        "fee_bps": cfg.fee_bps,
        "slippage_bps": cfg.slippage_bps,
        "final_price": candles[-1].close if candles else None,
        "candles_used": len(candles),
    })
    return {
        "signal_id": signal_id,
        "signal": signal,
        "record": {
            "status": "tested" if result.entry_triggered else "entry_not_triggered",
            "return_pct": ret,
            "exchange": candidate.exchange,
            "market_symbol": candidate.symbol,
            "result_json": payload,
        },
    }


def _build_summary(
    *,
    source_id: int,
    source_identifier: str,
    source_config: dict[str, Any],
    cfg: AnalyzeSourceRequest,
    info: dict[str, Any],
    records: list[SignalBacktestRecord],
) -> dict[str, Any]:
    returns: list[float] = []
    return_records: list[dict[str, Any]] = []
    stop_hits = entry_triggered = market_data_missing = unsupported = 0
    target_hits: Counter[int] = Counter()
    target_denominators: Counter[int] = Counter()
    exchange_usage: Counter[str] = Counter()
    mfe_values: list[float] = []
    mae_values: list[float] = []

    for rec in records:
        payload = rec.result_json or {}
        if rec.status == "no_market_data":
            market_data_missing += 1
        if rec.status == "unsupported":
            unsupported += 1
        if rec.exchange:
            exchange_usage[rec.exchange] += 1
        if payload.get("entry_triggered"):
            entry_triggered += 1
        if payload.get("stop_hit"):
            stop_hits += 1
        if payload.get("mfe_pct") is not None:
            mfe_values.append(float(payload["mfe_pct"]))
        if payload.get("mae_pct") is not None:
            mae_values.append(float(payload["mae_pct"]))
        if payload.get("entry_triggered"):
            for i, target in enumerate(payload.get("targets") or [], start=1):
                target_denominators[i] += 1
                if target.get("hit"):
                    target_hits[i] += 1
        if rec.return_pct is not None:
            value = float(rec.return_pct)
            returns.append(value)
            return_records.append({"return_pct": value, "symbol": rec.symbol, "timestamp": rec.timestamp})

    metrics = summarize_returns(returns, cfg.portfolio_fraction)

    def grouped(key_fn):
        groups: dict[str, list[float]] = {}
        for rec in return_records:
            groups.setdefault(str(key_fn(rec)), []).append(float(rec["return_pct"]))
        return {
            k: {
                "trades": len(v),
                "average_return_pct": sum(v) / len(v),
                "positive_trade_rate_pct": sum(1 for x in v if x > 0) / len(v) * 100,
                "sum_return_pct": sum(v),
            }
            for k, v in sorted(groups.items())
        }

    eligible_count = len(info["eligible"])
    processed_count = len(records)
    summary: dict[str, Any] = {
        "engine": "durable_chunked_v2.2",
        "source_id": source_id,
        "source_identifier": source_identifier,
        "history_complete": bool((source_config or {}).get("history_complete", False)),
        "signals_total_considered": len(info["all_rows"]),
        "signal_status_counts": dict(info["status_counts"]),
        "signal_side_counts": dict(info["side_counts"]),
        "signals_parsed_buy": len(info["parsed_buy"]),
        "signals_eligible": eligible_count,
        "signals_processed": processed_count,
        "signals_remaining": max(0, eligible_count - processed_count),
        "edited_snapshot_count": info["edited_snapshot_count"],
        "excluded_edited": info["excluded_edited"],
        "excluded_missing_stop": info["excluded_missing_stop"],
        "excluded_missing_entry": info["excluded_missing_entry"],
        "excluded_missing_targets": info["excluded_missing_targets"],
        "market_data_missing": market_data_missing,
        "unsupported": unsupported,
        "entry_triggered": entry_triggered,
        "entry_trigger_rate_pct": entry_triggered / eligible_count * 100 if eligible_count else None,
        "stop_hit_rate_pct": stop_hits / entry_triggered * 100 if entry_triggered else None,
        "target_hit_rates_pct": {
            f"tp{i}": target_hits[i] / target_denominators[i] * 100
            for i in sorted(target_denominators)
            if target_denominators[i]
        },
        "average_mfe_pct": sum(mfe_values) / len(mfe_values) if mfe_values else None,
        "average_mae_pct": sum(mae_values) / len(mae_values) if mae_values else None,
        "exchange_usage": dict(exchange_usage),
        "performance_by_symbol": grouped(lambda r: r["symbol"]),
        "performance_by_month": grouped(lambda r: r["timestamp"].strftime("%Y-%m")),
        "performance_by_year": grouped(lambda r: r["timestamp"].strftime("%Y")),
        "performance_by_weekday": grouped(lambda r: r["timestamp"].strftime("%A")),
        "performance_by_hour_utc": grouped(lambda r: f"{r['timestamp'].hour:02d}:00"),
        "assumptions": {
            "timeframe": cfg.timeframe,
            "horizon_days": cfg.horizon_days,
            "fee_bps": cfg.fee_bps,
            "slippage_bps": cfg.slippage_bps,
            "portfolio_fraction": cfg.portfolio_fraction,
            "require_stop": cfg.require_stop,
            "exclude_edited": cfg.exclude_edited,
            "conservative_intrabar": cfg.conservative_intrabar,
            "exit_allocation": "equal_weight_across_targets; unresolved remainder exits at stop or horizon close",
        },
        **metrics,
    }

    warnings: list[str] = []
    if not summary["history_complete"]:
        warnings.append("Channel history is partial; this run is a preview.")
    if not cfg.exclude_edited and info["edited_snapshot_count"]:
        warnings.append(
            f"{info['edited_snapshot_count']} recommendations are current snapshots of edited Telegram messages. "
            "Pre-edit text is unavailable unless Talos observed the edit live."
        )
    if not eligible_count:
        warnings.append(
            "No eligible parsed BUY signals. Check the exclusion counters below. Exchange API keys are not required for historical backtesting."
        )
    if processed_count and market_data_missing == processed_count:
        warnings.append(
            "All processed eligible signals are missing public historical market data. Inspect individual trade errors; this can happen for delisted/renamed pairs or exchange history limits."
        )
    if warnings:
        summary["warnings"] = warnings
    return summary


async def advance_run(run_id: int, batch_size: int = 4) -> AnalysisRunRecord:
    """Advance a run by a small durable batch and persist progress.

    This endpoint is intentionally restart-safe. The browser can close, Render can
    sleep, or the process can restart; the next call resumes by querying which
    signal_ids already have SignalBacktestRecord rows for this run.
    """
    batch_size = max(1, min(int(batch_size), 12))

    with SessionLocal() as db:
        run = db.get(AnalysisRunRecord, run_id)
        if not run:
            raise ValueError("Analysis run not found")
        source = db.get(SourceRecord, run.source_id)
        if not source:
            run.status = "error"
            run.stage = "source"
            run.error = "Source not found"
            run.completed_at = _utcnow()
            db.commit(); db.refresh(run)
            return run
        if run.status in {"complete", "error"}:
            return run
        cfg = AnalyzeSourceRequest(**(run.config_json or {}))
        # Snapshot every scalar needed after this SQLAlchemy session closes.
        # SQLAlchemy expires ORM attributes on commit by default, so keeping a
        # SourceRecord/AnalysisRunRecord outside this block can raise
        # DetachedInstanceError on PostgreSQL/Render.
        source_id = int(source.id)
        source_identifier = str(source.identifier)
        source_config = dict(source.config or {})
        if run.started_at is None:
            run.started_at = _utcnow()
        run.status = "running"
        run.stage = "eligibility"
        db.commit()

    info = _eligibility(source_identifier, cfg)
    eligible = info["eligible"]

    with SessionLocal() as db:
        existing_ids = {
            int(x[0]) for x in db.query(SignalBacktestRecord.signal_id)
            .filter(SignalBacktestRecord.analysis_run_id == run_id)
            .all()
        }
        records = (
            db.query(SignalBacktestRecord)
            .filter(SignalBacktestRecord.analysis_run_id == run_id)
            .order_by(SignalBacktestRecord.timestamp.asc())
            .all()
        )

    remaining = [x for x in eligible if int(x["id"]) not in existing_ids]
    if not eligible:
        summary = _build_summary(source_id=source_id, source_identifier=source_identifier, source_config=source_config, cfg=cfg, info=info, records=records)
        with SessionLocal() as db:
            row = db.get(AnalysisRunRecord, run_id)
            row.status = "complete"; row.stage = "complete"; row.progress = 1.0
            row.summary_json = summary; row.completed_at = _utcnow(); db.commit(); db.refresh(row)
            return row

    if remaining:
        batch = remaining[:batch_size]
        semaphore = asyncio.Semaphore(min(4, batch_size))
        outcomes = await asyncio.gather(*(_analyze_one(item, cfg, semaphore) for item in batch))
        with SessionLocal() as db:
            for outcome in outcomes:
                signal: NormalizedSignal = outcome["signal"]
                record = outcome["record"]
                # Idempotence: if a retry races with a prior request, do not duplicate.
                exists = (
                    db.query(SignalBacktestRecord)
                    .filter(SignalBacktestRecord.analysis_run_id == run_id)
                    .filter(SignalBacktestRecord.signal_id == int(outcome["signal_id"]))
                    .first()
                )
                if exists:
                    continue
                db.add(SignalBacktestRecord(
                    analysis_run_id=run_id,
                    source_id=source_id,
                    signal_id=int(outcome["signal_id"]),
                    timestamp=signal.timestamp,
                    symbol=signal.symbol,
                    exchange=record["exchange"],
                    market_symbol=record["market_symbol"],
                    status=record["status"],
                    return_pct=record["return_pct"],
                    result_json=record["result_json"],
                ))
            db.commit()

    with SessionLocal() as db:
        records = (
            db.query(SignalBacktestRecord)
            .filter(SignalBacktestRecord.analysis_run_id == run_id)
            .order_by(SignalBacktestRecord.timestamp.asc())
            .all()
        )

    summary = _build_summary(source_id=source_id, source_identifier=source_identifier, source_config=source_config, cfg=cfg, info=info, records=records)
    processed = len(records)
    complete = processed >= len(eligible)
    progress = 1.0 if complete else min(0.99, 0.05 + 0.94 * processed / max(1, len(eligible)))

    with SessionLocal() as db:
        row = db.get(AnalysisRunRecord, run_id)
        row.status = "complete" if complete else "running"
        row.stage = "complete" if complete else f"market_data:{processed}/{len(eligible)}"
        row.progress = progress
        row.summary_json = summary
        row.error = None
        if complete:
            row.completed_at = _utcnow()
        db.commit(); db.refresh(row)
        return row
