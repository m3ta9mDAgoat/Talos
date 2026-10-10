from __future__ import annotations

import asyncio
from collections import Counter, OrderedDict
from sqlalchemy import or_
from app.services.signal_parser import telegram_signal_parser
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.core.enums import MarketType, Side, SignalStatus
from app.db.session import SessionLocal
from app.models.analysis import AnalysisRunRecord, SignalBacktestRecord
from app.models.signal import SignalRecord
from app.models.raw_event import RawEventRecord
from app.models.processing import ProcessingStateRecord
from sqlalchemy import func
from app.models.source import SourceRecord
from app.models.telegram_audit import TelegramSignalVersionRecord
from app.schemas.analysis import AnalyzeSourceRequest
from app.schemas.signal import EntrySpec, NormalizedSignal, StopSpec
from app.services.backtest import signal_backtester
from app.services.market_data import market_data_resolver
from app.services.trade_metrics import summarize_returns, trade_return_pct



# Only a single replay's validated signal candidates are retained at once.
# These are disposable process-local accelerators, NOT checkpoints: after a
# Render restart the source is parsed again and SQL trade rows are authoritative.
_REPLAY_PREPARED: OrderedDict[int, dict[str, Any]] = OrderedDict()
# A restart rebuilds this disposable set from SQL. SQL rows are the durable truth.
_REPLAY_COMPLETED: OrderedDict[int, set[int]] = OrderedDict()


async def _prepared_replay(run_id: int, source_id: int, source_identifier: str,
                           cfg: AnalyzeSourceRequest) -> dict[str, Any]:
    cached = _REPLAY_PREPARED.get(run_id)
    if cached is not None:
        _REPLAY_PREPARED.move_to_end(run_id)
        return cached
    # Parsing thousands of Telegram messages is CPU / database work.  Never
    # execute it synchronously on a worker asyncio loop (the heartbeat needs
    # that loop to stay responsive).
    prepared = await asyncio.to_thread(_eligibility, source_id, source_identifier, cfg)
    # The summary needs counts, not the SQLAlchemy ORM objects themselves.
    # Avoid retaining several thousand ORM records in a long-running process.
    prepared['all_rows'] = range(len(prepared['all_rows']))
    prepared['parsed_buy'] = range(len(prepared['parsed_buy']))
    _REPLAY_PREPARED[run_id] = prepared
    _REPLAY_PREPARED.move_to_end(run_id)
    while len(_REPLAY_PREPARED) > 1:
        _REPLAY_PREPARED.popitem(last=False)
    return prepared


def _release_replay(run_id: int) -> None:
    _REPLAY_PREPARED.pop(run_id, None)
    _REPLAY_COMPLETED.pop(run_id, None)


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



def _signal_from_version(row: TelegramSignalVersionRecord, canonical: SignalRecord) -> NormalizedSignal:
    entry = EntrySpec(**row.entry_json) if row.entry_json else None
    stop = StopSpec(**row.stop_json) if row.stop_json else None
    symbol = row.symbol or canonical.symbol
    parts = symbol.split("/")
    return NormalizedSignal(
        strategy_id=canonical.strategy_id,
        strategy_version=canonical.strategy_version,
        source=canonical.source,
        source_id=canonical.source_id,
        source_message_id=canonical.source_message_id,
        timestamp=row.original_message_at,
        market_type=MarketType(row.market_type or canonical.market_type),
        symbol=symbol,
        quote=parts[1] if len(parts)>1 else None,
        side=Side(row.side or canonical.side),
        entry=entry, stop=stop, targets=list(row.targets_json or []),
        confidence=float(row.confidence if row.confidence is not None else canonical.confidence),
        status=SignalStatus(row.status if row.status in {x.value for x in SignalStatus} else canonical.status),
        raw_text=row.raw_text or canonical.raw_text,
        metadata={**(canonical.metadata_json or {}), "replay_revision": "original_observed", "raw_event_id": row.raw_event_id},
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


def _eligibility(source_record_id: int, source_identifier: str, cfg: AnalyzeSourceRequest) -> dict[str, Any]:
    with SessionLocal() as db:
        all_rows = (db.query(SignalRecord).filter(SignalRecord.source_id == source_identifier)
                    .order_by(SignalRecord.timestamp.asc()).limit(cfg.max_signals).all())
        originals: dict[str, TelegramSignalVersionRecord] = {}
        if cfg.edit_policy == "strict":
            versions = (db.query(TelegramSignalVersionRecord)
                        .filter(TelegramSignalVersionRecord.source_id == source_record_id,
                                TelegramSignalVersionRecord.revision_type == "message")
                        .order_by(TelegramSignalVersionRecord.revision_at.asc(), TelegramSignalVersionRecord.id.asc()).all())
            for v in versions:
                if v.symbol and v.status == SignalStatus.PARSED.value:
                    originals.setdefault(str(v.source_message_id), v)

    status_counts = Counter(str(r.status) for r in all_rows)
    side_counts = Counter(str(r.side) for r in all_rows)
    parsed_buy = [r for r in all_rows if str(r.status) == SignalStatus.PARSED.value and str(r.side) == Side.BUY.value]

    eligible: list[dict[str, Any]] = []
    excluded_edited = excluded_no_stop = excluded_missing_entry = excluded_missing_targets = excluded_no_original = 0
    edited_snapshot_count = 0
    reparsed_canonical_count = 0
    for r in parsed_buy:
        metadata = dict(r.metadata_json or {})
        edit_date = metadata.get("edit_date")
        if edit_date:
            edited_snapshot_count += 1
        if cfg.exclude_edited and edit_date:
            excluded_edited += 1; continue

        signal = _signal_from_record(r)
        replay_mode = cfg.edit_policy
        if edit_date and replay_mode == "strict":
            original = originals.get(str(r.source_message_id))
            if original is None:
                excluded_no_original += 1; continue
            signal = _signal_from_version(original, r)
        elif edit_date and replay_mode == "from_edit":
            try:
                edit_ts = datetime.fromisoformat(str(edit_date).replace("Z", "+00:00")).astimezone(timezone.utc)
                signal = signal.model_copy(update={"timestamp": edit_ts, "metadata": {**signal.metadata, "replay_revision": "edited_snapshot_from_edit_time"}})
            except Exception:
                excluded_no_original += 1; continue
        elif edit_date:
            signal = signal.model_copy(update={"metadata": {**signal.metadata, "replay_revision": "current_snapshot_from_original_time", "lookahead_risk": True}})

        # Old canonical records may have been marked PARSED with no TP because
        # the former generic parser did not recognize T1/T2 and emoji keycaps.
        # Recover the exact selected revision IN MEMORY, without mutating archive
        # data or overriding edited-message replay policy.
        if (not signal.targets or signal.entry is None or (cfg.require_stop and signal.stop is None)) and signal.raw_text:
            newer = telegram_signal_parser.parse(
                signal.raw_text,
                timestamp=signal.timestamp,
                source_id=source_identifier,
                source_message_id=str(r.source_message_id),
            ).signal
            if newer and newer.symbol == signal.symbol and newer.side == signal.side:
                additions = {}
                if not signal.targets and newer.targets:
                    additions['targets'] = newer.targets
                if signal.entry is None and newer.entry is not None:
                    additions['entry'] = newer.entry
                if signal.stop is None and newer.stop is not None:
                    additions['stop'] = newer.stop
                if additions:
                    reparsed_canonical_count += 1
                    signal = signal.model_copy(update={
                        **additions,
                        'metadata': {**signal.metadata, 'replay_reparsed_canonical': True,
                                     'replay_parser_version': 'adaptive_telegram_v4_7_5'},
                    })

        if cfg.require_stop and signal.stop is None:
            excluded_no_stop += 1; continue
        if signal.entry is None:
            excluded_missing_entry += 1; continue
        if not signal.targets:
            excluded_missing_targets += 1; continue
        eligible.append({"id": r.id, "signal": signal, "metadata": metadata})

    # Archive-first recovery: old classifiers frequently rejected channel-specific formatting.
    # Replay must not depend entirely on canonical SignalRecord materialization.
    # Run recovery whenever the canonical parser has excluded records: a few
    # valid signals must never mask thousands of rejected archived messages.
    archived_examined = archived_parsed = archived_rejected = 0
    archived_reasons: Counter[str] = Counter()
    archived_samples: list[dict[str, Any]] = []
    archive_truncated = False
    if len(eligible) < len(parsed_buy) and cfg.edit_policy in {"strict", "from_edit", "optimistic"}:
        # Descending order ensures the latest known revision is evaluated once per message.
        # Fetch only the necessary columns to limit Neon transfer size and avoid ORM bloat.
        cap = min(12000, max(500, int(cfg.max_signals)))
        with SessionLocal() as db:
            rows = (db.query(RawEventRecord.id, RawEventRecord.external_id, RawEventRecord.occurred_at,
                             RawEventRecord.text, RawEventRecord.event_type, RawEventRecord.metadata_json)
                    .filter(RawEventRecord.source_identifier == source_identifier)
                    .order_by(RawEventRecord.id.desc()).limit(cap + 1).all())
        archive_truncated = len(rows) > cap
        rows = rows[:cap]
        latest: dict[str, Any] = {}
        earliest: dict[str, Any] = {}
        for raw in rows:
            key = str(raw.external_id)
            latest.setdefault(key, raw)
            earliest[key] = raw
        eligible_ids = {str(row['signal'].source_message_id) for row in eligible}
        for key, raw in latest.items():
            if key in eligible_ids:
                continue
            archived_examined += 1
            original = earliest[key]
            if str(raw.event_type or '').lower() == 'delete':
                archived_reasons['deleted_message'] += 1
                continue
            if cfg.edit_policy == 'strict':
                if str(original.event_type or '').lower() != 'message' or (original.metadata_json or {}).get('edit_date'):
                    archived_reasons['original_revision_not_observed'] += 1
                    continue
                raw = original
            raw_meta = dict(raw.metadata_json or {})
            text = str(raw.text or '').strip()
            if not text:
                archived_reasons['empty_text'] += 1
                continue
            parsed = telegram_signal_parser.parse(text, timestamp=raw.occurred_at,
                        source_id=source_identifier, source_message_id=key,
                        default_quote='USDT')
            sig = parsed.signal
            if not sig:
                why = 'symbol_or_format_unrecognized'
            elif sig.side != Side.BUY:
                why = 'not_spot_buy'
            elif not sig.entry:
                why = 'entry_not_found'
            elif not sig.targets:
                why = 'targets_not_found'
            elif cfg.require_stop and not sig.stop:
                why = 'stop_not_found'
            elif sig.status != SignalStatus.PARSED:
                why = 'needs_review_inconsistent_fields'
            else:
                why = None
            if why:
                archived_rejected += 1
                archived_reasons[why] += 1
                if len(archived_samples) < 6 and why in {'symbol_or_format_unrecognized','entry_not_found','targets_not_found'}:
                    # Only a short snippet from the selected channel, shown to its operator.
                    archived_samples.append({'message_id': key, 'reason': why, 'text_preview': text[:220]})
                continue
            archived_parsed += 1
            meta = dict(sig.metadata or {})
            meta['replay_revision'] = 'archive_latest_at_original_time' if cfg.edit_policy == 'optimistic' else 'archive_replay'
            meta['lookahead_risk'] = cfg.edit_policy == 'optimistic' and (raw.id != original.id or bool(raw_meta.get('edit_date')))
            meta['archived_raw_event_id'] = raw.id
            meta['archive_first_recovery'] = True
            meta['original_message_at'] = original.occurred_at.isoformat()
            replay_at = original.occurred_at
            if replay_at.tzinfo is None:
                replay_at = replay_at.replace(tzinfo=timezone.utc)
            if cfg.edit_policy == 'from_edit' and (raw.id != original.id or bool(raw_meta.get('edit_date'))):
                edit_at = raw_meta.get('edit_date')
                try:
                    replay_at = datetime.fromisoformat(str(edit_at).replace('Z', '+00:00')) if edit_at else raw.occurred_at
                    if replay_at.tzinfo is None:
                        replay_at = replay_at.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    replay_at = raw.occurred_at
            sig = sig.model_copy(update={'timestamp':replay_at,'metadata':meta,'status':SignalStatus.PARSED})
            # Negative signal id references a raw archive snapshot, not a live SignalRecord.
            # signal_backtests.signal_id has no FK and permits these synthetic replay IDs.
            eligible.append({'id':-int(raw.id),'signal':sig,'metadata':raw_meta})
        eligible.sort(key=lambda x:x['signal'].timestamp)

    return {"all_rows":all_rows,"parsed_buy":parsed_buy,"eligible":eligible,"status_counts":status_counts,"side_counts":side_counts,
        "edited_snapshot_count":edited_snapshot_count,"reparsed_canonical_count":reparsed_canonical_count,
        "excluded_edited":excluded_edited,"excluded_no_original_revision":excluded_no_original,
        "excluded_missing_stop":excluded_no_stop,"excluded_missing_entry":excluded_missing_entry,"excluded_missing_targets":excluded_missing_targets,
        "archive_recovery_scanned":archived_examined, "archive_recovery_parsed":archived_parsed,
        "archive_recovery_rejected":archived_rejected, "archive_recovery_reasons":dict(archived_reasons),
        "archive_recovery_examples":archived_samples, "archive_recovery_truncated":archive_truncated}


async def _fetch_with_fallback(signal: NormalizedSignal, cfg: AnalyzeSourceRequest):
    end = signal.timestamp + timedelta(days=cfg.horizon_days)
    errors: list[str] = []
    any_resolved = False
    # Loading six full CCXT market catalogues at once causes enormous peak
    # memory pressure on small Render containers. Check exchanges in preference
    # order, only loading the next venue when the previous one cannot serve the
    # required historical window. Market existence must not imply candle data.
    for exchange in cfg.exchanges:
        try:
            candidates = await market_data_resolver.resolve(signal.symbol, [exchange])
        except Exception as exc:
            errors.append(f"{exchange}: catalogue error {type(exc).__name__}: {exc}")
            continue
        for candidate in candidates:
            any_resolved = True
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
    if not any_resolved:
        errors.append("Symbol is not currently resolvable on configured public CCXT spot venues.")
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
        "signal_provenance": signal.metadata,
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
        "archive_recovery_scanned":info.get('archive_recovery_scanned',0),
        "archive_recovery_parsed":info.get('archive_recovery_parsed',0),
        "archive_recovery_reasons":info.get('archive_recovery_reasons',{}),
        "archive_recovery_examples":info.get('archive_recovery_examples',[]),
        "archive_recovery_truncated":info.get('archive_recovery_truncated',False),
        "signal_status_counts": dict(info["status_counts"]),
        "signal_side_counts": dict(info["side_counts"]),
        "signals_parsed_buy": len(info["parsed_buy"]),
        "signals_eligible": eligible_count,
        "reparsed_canonical_count": info.get("reparsed_canonical_count", 0),
        "signals_processed": processed_count,
        "signals_remaining": max(0, eligible_count - processed_count),
        "edited_snapshot_count": info["edited_snapshot_count"],
        "excluded_edited": info["excluded_edited"],
        "excluded_missing_stop": info["excluded_missing_stop"],
        "excluded_missing_entry": info["excluded_missing_entry"],
        "excluded_missing_targets": info["excluded_missing_targets"],
        "excluded_no_original_revision": info.get("excluded_no_original_revision", 0),
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
            "edit_policy": cfg.edit_policy,
            "conservative_intrabar": cfg.conservative_intrabar,
            "exit_allocation": "equal_weight_across_targets; unresolved remainder exits at stop or horizon close",
        },
        **metrics,
    }

    warnings: list[str] = []
    if not summary["history_complete"]:
        warnings.append("Channel history is partial; this run is a preview.")
    if info["edited_snapshot_count"]:
        warnings.append(f"Edit replay policy: {cfg.edit_policy}. Edited snapshots: {info['edited_snapshot_count']}; strict exclusions without an observed original: {info.get('excluded_no_original_revision',0)}.")
    if info.get('reparsed_canonical_count'):
        warnings.append(f"Recovered trading levels from {info['reparsed_canonical_count']} stored signals with channel-specific formats.")
    if info.get('archive_recovery_parsed'):
        warnings.append(f"Archive-first replay recovered {info['archive_recovery_parsed']} eligible recommendations excluded by the old signal-classification pipeline.")
    if cfg.edit_policy == 'optimistic':
        warnings.append('LOOK-AHEAD BIAS: edited message contents are assumed known at the original posting time. Research only; not an executable historical strategy.')
    if not eligible_count:
        warnings.append(
            "NO ELIGIBLE SIGNALS — no trades were evaluated. Inspect source readiness: archive, processing, classification and rejection counters. This is not a successful backtest."
        )
    if processed_count and market_data_missing == processed_count:
        warnings.append(
            "All processed eligible signals are missing public historical market data. Inspect individual trade errors; this can happen for delisted/renamed pairs or exchange history limits."
        )
    if not eligible_count:
        summary["outcome"] = "no_eligible_signals"
        summary["diagnostic"] = "Run completed without trade results. The replay cannot produce trades unless archived Telegram messages are parsed into eligible BUY signals with entry and targets."
    elif processed_count and market_data_missing == processed_count:
        summary["outcome"] = "no_market_data"
    else:
        summary["outcome"] = "evaluated"
    if warnings:
        summary["warnings"] = warnings
    return summary


async def advance_run(run_id: int, batch_size: int = 1,
                      progress_hook: Callable[[str, float], None] | None = None) -> AnalysisRunRecord:
    """Evaluate exactly one Telegram signal and checkpoint it before returning.

    Resume uses stored SignalBacktestRecord rows. Parsing the archive and loading
    prior completion IDs happen once per process/run, not for every signal.
    Full metrics are recalculated every 50 results and at completion to avoid
    quadratic database reads on large archives.
    """
    with SessionLocal() as db:
        run = db.get(AnalysisRunRecord, run_id)
        if not run:
            raise ValueError("Analysis run not found")
        src = db.get(SourceRecord, run.source_id)
        if src is None:
            run.status = 'error'; run.error = 'Source not found'; run.stage = 'source'
            run.completed_at = _utcnow(); db.commit(); db.refresh(run)
            return run
        if run.status in {'complete', 'error', 'no_data'}:
            return run
        cfg = AnalyzeSourceRequest(**(run.config_json or {}))
        source_id, source_identifier = int(src.id), str(src.identifier)
        source_config = dict(src.config or {})
        if run.started_at is None:
            run.started_at = _utcnow()
        run.status = 'running'; run.stage = 'preparing_signals'
        db.commit()

    info = await _prepared_replay(run_id, source_id, source_identifier, cfg)
    eligible = info['eligible']
    total = len(eligible)
    if run_id not in _REPLAY_COMPLETED:
        with SessionLocal() as db:
            prior = set(int(x[0]) for x in db.query(SignalBacktestRecord.signal_id).filter(
                SignalBacktestRecord.analysis_run_id == run_id).all())
        _REPLAY_COMPLETED[run_id] = prior
        _REPLAY_COMPLETED.move_to_end(run_id)
        while len(_REPLAY_COMPLETED) > 1:
            _REPLAY_COMPLETED.popitem(last=False)
    processed_ids = _REPLAY_COMPLETED[run_id]
    processed = len(processed_ids)

    if not total:
        summary = _build_summary(source_id=source_id, source_identifier=source_identifier,
                  source_config=source_config, cfg=cfg, info=info, records=[])
        with SessionLocal() as db:
            run = db.get(AnalysisRunRecord, run_id)
            run.status = 'no_data'; run.stage = 'no_eligible_signals'; run.progress = 1.0
            run.summary_json = summary; run.completed_at = _utcnow()
            db.commit(); db.refresh(run)
        _release_replay(run_id)
        return run

    next_item = next((item for item in eligible if int(item['id']) not in processed_ids), None)
    if next_item is not None:
        symbol = next_item['signal'].symbol
        stage = f'Market data · trade {processed + 1:,}/{total:,} · {symbol}'
        if progress_hook:
            progress_hook(stage, 0.05 + 0.94 * (processed / total))
        # Exactly one network-backed trade at a time; venue fallback inside
        # _analyze_one is sequential too (no fan-out of six catalogues).
        outcome = await _analyze_one(next_item, cfg, asyncio.Semaphore(1))
        record = outcome['record']
        signal: NormalizedSignal = outcome['signal']
        sid = int(outcome['signal_id'])
        with SessionLocal() as db:
            exists = db.query(SignalBacktestRecord.id).filter(
                SignalBacktestRecord.analysis_run_id == run_id,
                SignalBacktestRecord.signal_id == sid).first()
            if not exists:
                db.add(SignalBacktestRecord(
                    analysis_run_id=run_id, source_id=source_id, signal_id=sid,
                    timestamp=signal.timestamp, symbol=signal.symbol,
                    exchange=record['exchange'], market_symbol=record['market_symbol'],
                    status=record['status'], return_pct=record['return_pct'],
                    result_json=record['result_json']))
                db.commit()  # Durable checkpoint BEFORE advancing the cursor.
        processed_ids.add(sid)
        processed = len(processed_ids)
    else:
        processed = len(processed_ids)

    complete = processed >= total
    progress = 1.0 if complete else min(0.99, 0.05 + 0.94 * processed / total)
    # A full summary after every trade would be O(N^2) over the SQL result table.
    refresh_summary = complete or processed == 1 or processed % 50 == 0
    summary = None
    if refresh_summary:
        with SessionLocal() as db:
            records = db.query(SignalBacktestRecord).filter(
                SignalBacktestRecord.analysis_run_id == run_id).order_by(
                SignalBacktestRecord.timestamp.asc()).all()
        summary = _build_summary(source_id=source_id, source_identifier=source_identifier,
                  source_config=source_config, cfg=cfg, info=info, records=records)

    with SessionLocal() as db:
        run = db.get(AnalysisRunRecord, run_id)
        prior_summary = dict(run.summary_json or {})
        if summary is None:
            summary = {**prior_summary, 'signals_eligible': total,
                       'signals_processed': processed,
                       'signals_remaining': max(0, total - processed),
                       'outcome': 'in_progress'}
        run.progress = progress
        run.stage = 'complete' if complete else f'market_data:{processed}/{total}'
        run.status = 'complete' if complete else 'running'
        run.summary_json = summary
        run.error = None
        if complete:
            run.completed_at = _utcnow()
        db.commit(); db.refresh(run)
    if progress_hook:
        progress_hook(f'{"Complete" if complete else "Evaluated"} · {processed:,}/{total:,} trades', progress)
    if complete:
        _release_replay(run_id)
    return run


def replay_readiness(source_id: int, config: AnalyzeSourceRequest | None = None) -> dict[str, Any]:
    """Show observed archive/processing/eligibility counts; never invent replay output."""
    from app.services.processing import source_processing_stats
    cfg = config or AnalyzeSourceRequest()
    with SessionLocal() as db:
        src = db.get(SourceRecord, int(source_id))
        if not src:
            raise ValueError("Source not found")
        sid, identifier, name = src.id, src.identifier, src.name
        history_complete = bool((src.config or {}).get("history_complete", False))
        st = source_processing_stats(db, src)
    info = _eligibility(sid, identifier, cfg)
    count = len(info["eligible"])
    parsed = len(info["parsed_buy"])
    reasons = []
    if not st["raw_rows"]:
        reasons.append("No Telegram messages archived for this source. Import / verify its history in Sources first.")
    if st["raw_rows"] and not st["processed_states"] and not count:
        reasons.append("Archived messages have not been classified. Run Process in Sources and wait for the job to finish.")
    elif st["raw_rows"] > st["processed_states"] and not count:
        reasons.append(f"{st['raw_rows'] - st['processed_states']} archived revisions may be unprocessed; run Process in Sources.")
    if st["processed_states"] and not st["signals"] and not count:
        reasons.append("Messages were processed, but no trade signals were parsed. Inspect classification totals and source message format; importing more history may help.")
    if st["signals"] and not parsed and not count:
        reasons.append("Signals exist, but none is a PARSED BUY for this source. Inspect signal statuses and directions.")
    if parsed and not count:
        reasons.append("BUY signals were found, but entry / targets / edit-policy / stop requirements exclude them. Inspect rejection counters.")
    if not st["raw_rows"] and st["signals"]:
        reasons.append("Signals exist without matching archived source rows; check source identifier mapping.")
    if not reasons and count:
        reasons.append(f"{count} eligible BUY signals ready for historical market-data evaluation.")
    return {
        "source_id": sid, "name": name, "identifier": identifier,
        "raw_events": st["raw_rows"], "unique_messages": st["unique_messages"],
        "processed_revisions": st["processed_states"], "processing_status": st["processing_status"],
        "classifications": st["classifications"], "signals_stored": st["signals"],
        "signals_considered": len(info["all_rows"]), "signal_status_counts": dict(info["status_counts"]),
        "signal_side_counts": dict(info["side_counts"]), "parsed_buy": parsed, "eligible_buy": count,
        "excluded_missing_entry": info["excluded_missing_entry"],
        "excluded_missing_targets": info["excluded_missing_targets"],
        "reparsed_canonical_count": info.get("reparsed_canonical_count", 0),
        "excluded_missing_stop": info["excluded_missing_stop"],
        "excluded_edited": info["excluded_edited"],
        "excluded_no_original_revision": info["excluded_no_original_revision"],
        "archive_recovery_scanned": info.get("archive_recovery_scanned",0),
        "archive_recovery_parsed": info.get("archive_recovery_parsed",0),
        "archive_recovery_reasons": info.get("archive_recovery_reasons",{}),
        "archive_recovery_examples": info.get("archive_recovery_examples",[]),
        "archive_recovery_truncated": info.get("archive_recovery_truncated",False),
        "history_complete": history_complete,
        "reasons": reasons,
        "ready": count > 0,
    }
