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
from app.services.stored_signal_service import parse_all_stored_messages
from app.services.trade_metrics import summarize_returns, trade_return_pct


_tasks: dict[int, asyncio.Task] = {}


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


def _save_run(
    run_id: int,
    *,
    status: str | None = None,
    stage: str | None = None,
    progress: float | None = None,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
    completed: bool = False,
) -> None:
    with SessionLocal() as db:
        row = db.get(AnalysisRunRecord, run_id)
        if not row:
            return
        if status is not None:
            row.status = status
        if stage is not None:
            row.stage = stage
        if progress is not None:
            row.progress = max(0.0, min(1.0, progress))
        if summary is not None:
            row.summary_json = summary
        if error is not None:
            row.error = error
        if completed:
            row.completed_at = _utcnow()
        db.commit()


def create_run(source_id: int, request: AnalyzeSourceRequest) -> AnalysisRunRecord:
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
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def start_run(run_id: int) -> None:
    if run_id in _tasks and not _tasks[run_id].done():
        return
    _tasks[run_id] = asyncio.create_task(_run(run_id))


async def _fetch_with_fallback(signal: NormalizedSignal, cfg: AnalyzeSourceRequest):
    end = signal.timestamp + timedelta(days=cfg.horizon_days)
    candidates = await market_data_resolver.resolve(signal.symbol, cfg.exchanges)
    errors: list[str] = []
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
            errors.append(f"{candidate.exchange}: {exc}")
            continue
        if candles:
            return candidate, candles, errors
        errors.append(f"{candidate.exchange}: no candles returned")
    return None, [], errors


async def _analyze_one(item: dict[str, Any], cfg: AnalyzeSourceRequest, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    signal_id = int(item["id"])
    signal: NormalizedSignal = item["signal"]

    if signal.market_type != MarketType.SPOT or signal.side != Side.BUY:
        return {
            "kind": "unsupported",
            "signal_id": signal_id,
            "signal": signal,
            "record": {
                "status": "unsupported",
                "return_pct": None,
                "exchange": None,
                "market_symbol": None,
                "result_json": {"reason": "V1 profitability analysis executes long spot BUY signals only."},
            },
        }

    async with semaphore:
        candidate, candles, fetch_errors = await _fetch_with_fallback(signal, cfg)

    if not candles or candidate is None:
        return {
            "kind": "no_market_data",
            "signal_id": signal_id,
            "signal": signal,
            "record": {
                "status": "no_market_data",
                "return_pct": None,
                "exchange": None,
                "market_symbol": None,
                "result_json": {"errors": fetch_errors[-6:]},
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
    payload.update(
        {
            "fetch_errors": fetch_errors[-6:],
            "timeframe": cfg.timeframe,
            "horizon_days": cfg.horizon_days,
            "fee_bps": cfg.fee_bps,
            "slippage_bps": cfg.slippage_bps,
            "final_price": candles[-1].close if candles else None,
        }
    )
    return {
        "kind": "tested",
        "signal_id": signal_id,
        "signal": signal,
        "candidate": candidate,
        "backtest": result,
        "return_pct": ret,
        "record": {
            "status": "tested" if result.entry_triggered else "entry_not_triggered",
            "return_pct": ret,
            "exchange": candidate.exchange,
            "market_symbol": candidate.symbol,
            "result_json": payload,
        },
    }


async def _run(run_id: int) -> None:
    # Give FastAPI a chance to send the 202-style run response before any heavy work.
    await asyncio.sleep(0)

    with SessionLocal() as db:
        run = db.get(AnalysisRunRecord, run_id)
        if not run:
            return
        source = db.get(SourceRecord, run.source_id)
        if not source:
            _save_run(run_id, status="error", stage="source", error="Source not found", completed=True)
            return
        cfg = AnalyzeSourceRequest(**(run.config_json or {}))
        run.status = "running"
        run.stage = "classifying_and_parsing"
        run.progress = 0.02
        run.started_at = _utcnow()
        db.commit()
        source_id = source.id
        source_identifier = source.identifier
        history_complete = bool((source.config or {}).get("history_complete", False))

    try:
        # Parsing is synchronous DB/CPU work. Offload it so it cannot freeze the
        # FastAPI event loop and turn progress/API requests into proxy 502 pages.
        parse_counts = await asyncio.to_thread(parse_all_stored_messages, source_id)
        _save_run(run_id, stage="loading_signals", progress=0.08)

        with SessionLocal() as db:
            rows = (
                db.query(SignalRecord)
                .filter(SignalRecord.source_id == source_identifier)
                .filter(SignalRecord.status == SignalStatus.PARSED.value)
                .filter(SignalRecord.side == Side.BUY.value)
                .order_by(SignalRecord.timestamp.asc())
                .limit(cfg.max_signals)
                .all()
            )
            signal_rows = [
                {"id": r.id, "signal": _signal_from_record(r), "metadata": dict(r.metadata_json or {})}
                for r in rows
            ]

        eligible: list[dict[str, Any]] = []
        excluded_edited = 0
        excluded_no_stop = 0
        for item in signal_rows:
            signal = item["signal"]
            metadata = item["metadata"]
            if cfg.exclude_edited and metadata.get("edit_date"):
                excluded_edited += 1
                continue
            if cfg.require_stop and signal.stop is None:
                excluded_no_stop += 1
                continue
            if signal.entry is None or not signal.targets:
                continue
            eligible.append(item)

        with SessionLocal() as db:
            db.query(SignalBacktestRecord).filter(SignalBacktestRecord.analysis_run_id == run_id).delete()
            db.commit()

        total = max(1, len(eligible))
        returns: list[float] = []
        stop_hits = 0
        entry_triggered = 0
        data_missing = 0
        unsupported = 0
        target_hits: Counter[int] = Counter()
        target_denominators: Counter[int] = Counter()
        exchange_usage: Counter[str] = Counter()
        mfe_values: list[float] = []
        mae_values: list[float] = []

        # Limited concurrency keeps the UI responsive and overlaps requests across
        # exchanges without turning Talos into a rate-limit cannon.
        semaphore = asyncio.Semaphore(4)
        tasks = [asyncio.create_task(_analyze_one(item, cfg, semaphore)) for item in eligible]
        pending_rows: list[SignalBacktestRecord] = []

        completed_count = 0
        for future in asyncio.as_completed(tasks):
            outcome = await future
            completed_count += 1
            signal: NormalizedSignal = outcome["signal"]
            record = outcome["record"]
            kind = outcome["kind"]

            if kind == "unsupported":
                unsupported += 1
            elif kind == "no_market_data":
                data_missing += 1
            else:
                result = outcome["backtest"]
                candidate = outcome["candidate"]
                exchange_usage[candidate.exchange] += 1
                if result.entry_triggered:
                    entry_triggered += 1
                if result.stop_hit:
                    stop_hits += 1
                if result.mfe_pct is not None:
                    mfe_values.append(result.mfe_pct)
                if result.mae_pct is not None:
                    mae_values.append(result.mae_pct)
                for i, hit in enumerate(result.targets, start=1):
                    target_denominators[i] += 1 if result.entry_triggered else 0
                    if hit.hit:
                        target_hits[i] += 1
                if outcome.get("return_pct") is not None:
                    returns.append(float(outcome["return_pct"]))

            pending_rows.append(
                SignalBacktestRecord(
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
                )
            )

            if len(pending_rows) >= 20 or completed_count == len(eligible):
                with SessionLocal() as db:
                    db.add_all(pending_rows)
                    db.commit()
                pending_rows = []

            if completed_count == 1 or completed_count % 5 == 0 or completed_count == len(eligible):
                progress = 0.10 + 0.86 * completed_count / total
                _save_run(run_id, stage=f"market_data:{completed_count}/{len(eligible)}", progress=progress)

        metrics = summarize_returns(returns, cfg.portfolio_fraction)
        target_rates = {
            f"tp{i}": round(target_hits[i] / target_denominators[i] * 100, 2)
            for i in sorted(target_denominators)
            if target_denominators[i]
        }
        summary: dict[str, Any] = {
            "source_id": source_id,
            "source_identifier": source_identifier,
            "history_complete": history_complete,
            "parser": parse_counts,
            "signals_parsed": len(signal_rows),
            "signals_eligible": len(eligible),
            "excluded_edited": excluded_edited,
            "excluded_missing_stop": excluded_no_stop,
            "market_data_missing": data_missing,
            "unsupported": unsupported,
            "entry_triggered": entry_triggered,
            "entry_trigger_rate_pct": round(entry_triggered / len(eligible) * 100, 2) if eligible else None,
            "stop_hit_rate_pct": round(stop_hits / entry_triggered * 100, 2) if entry_triggered else None,
            "target_hit_rates_pct": target_rates,
            "average_mfe_pct": round(sum(mfe_values) / len(mfe_values), 4) if mfe_values else None,
            "average_mae_pct": round(sum(mae_values) / len(mae_values), 4) if mae_values else None,
            "exchange_usage": dict(exchange_usage),
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
        if not history_complete:
            summary["warnings"] = ["Channel history is still partial. Treat this run as a preview, not the final channel score."]
        _save_run(run_id, status="complete", stage="complete", progress=1.0, summary=summary, completed=True)
    except Exception as exc:
        _save_run(run_id, status="error", stage="error", error=f"{type(exc).__name__}: {exc}", completed=True)
