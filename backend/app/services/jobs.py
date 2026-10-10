from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
import math
from typing import Any

from sqlalchemy import case, func, text, update
from app.db.session import SessionLocal
from app.models.jobs import JobRecord
from app.models.signal import SignalRecord
from app.models.raw_event import RawEventRecord
from app.models.processing import ProcessingStateRecord
from app.models.source import SourceRecord
from app.services.runtime_wake import JOB_WAKE_EVENT, wake_jobs


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_job(kind: str, payload: dict[str, Any], *, max_attempts: int = 20) -> JobRecord:
    with SessionLocal() as db:
        row = JobRecord(kind=kind, status="queued", payload_json=payload, max_attempts=max_attempts, next_run_at=utcnow())
        db.add(row); db.commit(); db.refresh(row)
        wake_jobs()
        return row


_DEDUPE_LOCK = threading.RLock()


def _replay_snapshot(db, source_id: int) -> str:
    """Change token when archived revisions or processing changes (including edits)."""
    source = db.get(SourceRecord, source_id)
    if source is None:
        raise ValueError("Source not found")
    raw = db.query(func.count(RawEventRecord.id), func.max(RawEventRecord.id)).filter(
        RawEventRecord.source_identifier == source.identifier).one()
    signal = db.query(func.count(SignalRecord.id), func.max(SignalRecord.id)).filter(
        SignalRecord.source_id == source.identifier).one()
    processed = db.query(func.count(ProcessingStateRecord.id), func.max(ProcessingStateRecord.processed_at)).filter(
        ProcessingStateRecord.source_id == source_id).one()
    return json.dumps([raw[0], raw[1], signal[0], signal[1], processed[0], str(processed[1])], separators=(',', ':'))


def create_or_reuse_user_job(kind: str, payload: dict[str, Any], *, max_attempts: int = 20) -> tuple[JobRecord, bool]:
    """Deduplicate repeat button presses, including across Render workers on Postgres.

    A replay is reusable even when complete, until new archived/processed input arrives.
    Other jobs are reusable while active, preventing accidental double execution.
    """
    clean = dict(payload)
    with _DEDUPE_LOCK:
        with SessionLocal() as db:
            source_id = int(clean.get('source_id', 0)) if clean.get('source_id') is not None else 0
            if kind == 'telegram_replay' and source_id:
                clean['_input_snapshot'] = 'replay-sequential-eta-bingx-v4.7.8|' + _replay_snapshot(db, source_id)
            sig = hashlib.sha256(json.dumps([kind, clean], sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()
            # Serialize simultaneous POST requests across instances using a Postgres advisory transaction lock.
            if db.bind.dialect.name == 'postgresql':
                lock_id = int(sig[:15], 16)
                db.execute(text('SELECT pg_advisory_xact_lock(:lock)'), {'lock': lock_id})
            recent = db.query(JobRecord).filter(JobRecord.kind == kind).order_by(JobRecord.id.desc()).limit(350).all()
            for old in recent:
                prior = dict(old.payload_json or {})
                same_source = source_id and int(prior.get('source_id', -1)) == source_id
                same_payload = hashlib.sha256(json.dumps([kind, prior], sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest() == sig
                if old.status in ('queued', 'running', 'retrying') and (same_payload or (kind in ('source_process', 'source_import_full') and same_source)):
                    return old, True
                if kind == 'telegram_replay' and same_payload and old.status in ('complete', 'no_data'):
                    return old, True
            row = JobRecord(kind=kind, status='queued', payload_json=clean, max_attempts=max_attempts, next_run_at=utcnow())
            db.add(row); db.commit(); db.refresh(row)
            wake_jobs()
            return row, False


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _job_eta(row: JobRecord) -> dict[str, Any]:
    """Honest estimates from measured throughput, shared by every UI job badge.

    For replay we use completed trades/total trades, not approximate 5% stage
    overhead. Generic jobs show an approximate ETA only after meaningful progress.
    Restart/retry time can skew estimates, so cap and clearly label them.
    """
    state = dict(row.state_json or {})
    started = _parse_utc(state.get('eta_started_at'))
    finished = row.status in {'complete', 'no_data', 'cancelled', 'error'}
    if finished:
        return {'eta_seconds': 0 if row.status in {'complete', 'no_data'} else None,
                'eta_at': None, 'eta_basis': 'finished',
                'work_completed': state.get('work_completed'), 'work_total': state.get('work_total')}
    if row.status == 'queued' and not started:
        return {'eta_seconds': None, 'eta_at': None, 'eta_basis': 'waiting'}
    if row.status == 'retrying':
        return {'eta_seconds': None, 'eta_at': None, 'eta_basis': 'retrying'}
    if not started:
        return {'eta_seconds': None, 'eta_at': None, 'eta_basis': 'warming_up'}
    elapsed = max(0.0, (utcnow() - started).total_seconds())
    completed, total = state.get('work_completed'), state.get('work_total')
    if completed is not None and total:
        completed, total = int(completed), int(total)
        # Use smoothed intervals BETWEEN persisted trade completions. Measuring
        # from job start would include one-time archive parsing and inflate ETA.
        observed = state.get('eta_trade_seconds_ema')
        if completed < 2 or observed is None:
            return {'eta_seconds': None, 'eta_at': None, 'eta_basis': 'measuring_trade_speed',
                    'work_completed': completed, 'work_total': total}
        seconds = min(30*24*3600, math.ceil(max(0, total-completed) * float(observed)))
        return {'eta_seconds': seconds, 'eta_at': (utcnow()+timedelta(seconds=seconds)).isoformat(),
                'eta_basis': 'observed_trades', 'work_completed': completed, 'work_total': total}
    progress = float(row.progress or 0)
    if elapsed >= 10 and 0.10 <= progress < 0.995:
        seconds = min(30*24*3600, math.ceil(elapsed * (1-progress) / progress))
        return {'eta_seconds': seconds, 'eta_at': (utcnow()+timedelta(seconds=seconds)).isoformat(),
                'eta_basis': 'observed_progress'}
    return {'eta_seconds': None, 'eta_at': None, 'eta_basis': 'measuring_speed'}


def job_dict(row: JobRecord) -> dict[str, Any]:
    return {
        "id": row.id, "kind": row.kind, "status": row.status, "progress": row.progress,
        "stage": (row.result_json or {}).get("stage") or (row.state_json or {}).get("stage") or ("Waiting for worker" if row.status == "queued" else "Processing"),
        "payload": row.payload_json or {}, "state": row.state_json or {}, "result": row.result_json or {},
        "attempts": row.attempts, "max_attempts": row.max_attempts, "next_run_at": row.next_run_at,
        "heartbeat_at": row.heartbeat_at, "error": row.error, "created_at": row.created_at,
        "updated_at": row.updated_at, "completed_at": row.completed_at,
        **_job_eta(row),
    }


def _backoff(attempt: int) -> int:
    return min(300, max(2, 2 ** min(attempt, 8)))


async def _handle(row: JobRecord) -> tuple[bool, float, dict[str, Any], dict[str, Any]]:
    p = dict(row.payload_json or {})
    state = dict(row.state_json or {})

    if row.kind == "source_import_full":
        from app.services.full_import import prepare, status, step, VERIFY_VERSION
        source_id = int(p["source_id"])
        st = status(source_id)
        if int(st.get("verification_version", 0)) != VERIFY_VERSION or not state.get("prepared"):
            st = await prepare(source_id, batch_size=int(p.get("batch_size", 1000)))
            state["prepared"] = True
        if not st.get("history_complete"):
            st = await step(source_id, batch_size=int(p.get("batch_size", 1000)))
        estimate = int(st.get("estimated_total") or 0)
        fetched = int(st.get("fetched_total") or 0)
        progress = min(0.98, fetched / estimate) if estimate else (0.99 if fetched else 0.0)
        if st.get("history_complete"):
            progress = 1.0
            if p.get("process_after", True) and not state.get("process_job_id"):
                child = create_job("source_process", {"source_id": source_id, "force": False}, max_attempts=10)
                state["process_job_id"] = child.id
            return True, progress, state, st
        if st.get("status") == "error":
            raise RuntimeError(st.get("last_error") or "Telegram import failed")
        return False, progress, state, st

    if row.kind == "source_process":
        from app.services.processing import process_source
        def update_progress(examined: int, total: int):
            # Called from worker thread at committed checkpoints, not from the browser.
            with SessionLocal() as progress_db:
                record = progress_db.get(JobRecord, row.id)
                if record and record.status == 'running':
                    record.progress = min(0.99, examined / total) if total else 0.99
                    record.state_json = {**(record.state_json or {}), 'stage': f'Classifying {examined:,}/{total:,} archived revisions'}
                    record.heartbeat_at = utcnow()
                    progress_db.commit()
        result = await asyncio.to_thread(process_source, int(p["source_id"]), force=bool(p.get("force", False)), progress_callback=update_progress)
        return True, 1.0, state, {**result, 'stage': 'Processed all pending archived revisions'}

    if row.kind == "source_sync":
        from app.services.universal_sources import sync_source
        from app.models.source import SourceRecord
        source_id = int(p["source_id"])
        result = await sync_source(source_id)
        if p.get("process_after", True):
            with SessionLocal() as db:
                source = db.get(SourceRecord, source_id)
                is_telegram = bool(source and str(getattr(source.type, "value", source.type)) == "telegram")
            if is_telegram:
                create_job("source_process", {"source_id": source_id, "force": False}, max_attempts=10)
        return True, 1.0, state, result if isinstance(result, dict) else {"result": str(result)}

    if row.kind == "telegram_replay":
        from app.schemas.analysis import AnalyzeSourceRequest
        from app.services.channel_analysis import create_run, advance_run
        run_id = state.get("run_id")
        if not run_id:
            cfg = AnalyzeSourceRequest(**dict(p.get("config") or {}))
            run = create_run(int(p["source_id"]), cfg)
            run_id = run.id; state["run_id"] = run_id
        def mark(stage: str, progress: float) -> None:
            # Save the run pointer and stage BEFORE the expensive archive scan.
            # Recovery after a container restart must reuse the original run.
            with SessionLocal() as checkpoint_db:
                active = checkpoint_db.get(JobRecord, row.id)
                if active and active.status == 'running':
                    active.state_json = {**(active.state_json or {}), **state, 'stage': stage}
                    active.result_json = {**(active.result_json or {}), 'run_id': int(run_id), 'stage': stage}
                    active.progress = max(float(active.progress or 0), float(progress))
                    active.heartbeat_at = utcnow()
                    checkpoint_db.commit()
        mark('Preparing eligible Telegram signals', 0.01)
        run = await advance_run(int(run_id), batch_size=1, progress_hook=mark)
        total = int((run.summary_json or {}).get('signals_eligible') or 0)
        completed = int((run.summary_json or {}).get('signals_processed') or 0)
        prior_completed = int(state.get('work_completed') or 0)
        now_sample = utcnow()
        previous_sample = _parse_utc(state.get('eta_last_trade_at'))
        if completed > prior_completed:
            if prior_completed > 0 and previous_sample is not None:
                delta = (now_sample - previous_sample).total_seconds()
                if 0 < delta < 600:
                    measured = delta / (completed - prior_completed)
                    previous_ema = state.get('eta_trade_seconds_ema')
                    state['eta_trade_seconds_ema'] = (0.75 * float(previous_ema) + 0.25 * measured) if previous_ema else measured
                    state['eta_samples'] = int(state.get('eta_samples') or 0) + 1
            state['eta_last_trade_at'] = now_sample.isoformat()
        state.update({'stage': run.stage, 'work_total': total, 'work_completed': completed})
        result = {"run_id": run.id, "status": run.status, "stage": run.stage, "summary": run.summary_json or {}, "error": run.error}
        if run.status == "error":
            raise RuntimeError(run.error or "Replay failed")
        return run.status in ("complete", "no_data"), float(run.progress or 0.0), state, {**result, "outcome": (run.summary_json or {}).get("outcome"), "stage": run.stage}

    if row.kind == "strategy_backtest":
        from app.services.strategy_backtest import create_strategy_run, advance_strategy_run
        run_id = state.get("run_id")
        if not run_id:
            run = create_strategy_run(int(p["strategy_id"]), dict(p.get("config") or {}))
            run_id = run.id; state["run_id"] = run_id
        run = await advance_strategy_run(int(run_id), batch_size=int(p.get("batch_size", 12)))
        result = {"run_id": run.id, "status": run.status, "stage": run.stage, "summary": run.summary_json or {}, "error": run.error}
        if run.status == "error":
            raise RuntimeError(run.error or "Strategy backtest failed")
        return run.status in ("complete", "no_data"), float(run.progress or 0.0), state, {**result, "outcome": (run.summary_json or {}).get("outcome"), "stage": run.stage}

    if row.kind == "news_alerts":
        from app.services.notifications import send_news_digest
        result = await send_news_digest()
        return True, 1.0, state, result

    if row.kind == "news_auto_paper":
        from app.services.news_trading import auto_paper_cycle
        result = await auto_paper_cycle()
        return True, 1.0, state, result

    if row.kind == "research_extract":
        from app.models.research import ResearchArtifactRecord
        from app.services.research_service import draft_strategy
        paper_id = int(p["paper_id"])
        with SessionLocal() as db:
            paper = db.get(ResearchArtifactRecord, paper_id)
            if not paper or paper.kind != "paper_upload":
                raise ValueError("Research paper not found")
            text = paper.notes or ""
            title = paper.title
        if not text:
            raise ValueError("Paper has no extracted text")
        spec = await draft_strategy(text, name=title)
        with SessionLocal() as db:
            paper = db.get(ResearchArtifactRecord, paper_id)
            meta = dict(paper.metadata_json or {}); meta["strategy_spec"] = spec; paper.metadata_json = meta; db.commit()
        return True, 1.0, state, {"paper_id": paper_id, "strategy_spec": spec}

    if row.kind == "optimization":
        from app.services.generic_optimizer import create_optimization, advance_optimization
        opt_id = state.get("optimization_id")
        if not opt_id:
            opt = create_optimization(int(p["strategy_id"]), dict(p.get("config") or {}), int(p.get("max_trials", 30)))
            opt_id = opt.id; state["optimization_id"] = opt_id
        opt = await advance_optimization(int(opt_id), run_batch_size=int(p.get("run_batch_size", 20)))
        result = {"optimization_id": opt.id, "status": opt.status, "best": opt.best_json or {}, "trials": opt.trials_json or [], "error": opt.error}
        if opt.status == "error": raise RuntimeError(opt.error or "Optimization failed")
        return opt.status == "complete", float(opt.progress or 0.0), state, result

    raise ValueError(f"Unknown job kind: {row.kind}")


async def _heartbeat_until_done(job_id: int, done_event: asyncio.Event):
    while not done_event.is_set():
        try:
            await asyncio.wait_for(done_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            with SessionLocal() as db:
                row = db.get(JobRecord, job_id)
                if row and row.status == "running":
                    row.heartbeat_at = utcnow()
                    db.commit()


async def run_one_job(job_id: int) -> dict[str, Any]:
    # A queued job can be visible to multiple Render instances. Claim it with
    # one SQL UPDATE rather than SELECT-then-SET to avoid duplicate evaluation.
    with SessionLocal() as db:
        claimed = db.execute(
            update(JobRecord)
            .where(JobRecord.id == job_id, JobRecord.status.in_(['queued', 'retrying']))
            .values(status='running', heartbeat_at=utcnow(), updated_at=utcnow(), error=None)
        ).rowcount
        db.commit()
        row = db.get(JobRecord, job_id)
        if not row:
            raise ValueError('Job not found')
        if claimed != 1:
            # Another instance owns this work, or it was cancelled/completed.
            return job_dict(row)
        if not (row.state_json or {}).get('eta_started_at'):
            row.state_json = {**(row.state_json or {}), 'eta_started_at': utcnow().isoformat()}
            db.commit()

    stop_heartbeat = asyncio.Event()
    heartbeat_task = asyncio.create_task(_heartbeat_until_done(job_id, stop_heartbeat))
    try:
        with SessionLocal() as db:
            snapshot = db.get(JobRecord, job_id)
        # Do not hold a PostgreSQL transaction or pool connection open while
        # fetching minutes of historical candles from public exchanges.
        done, progress, state, result = await _handle(snapshot)
        with SessionLocal() as db:
            row = db.get(JobRecord, job_id)
            if row.status == 'cancelled':
                return job_dict(row)
            row.progress = max(float(row.progress or 0.0), float(progress or 0.0))
            row.state_json = {**(row.state_json or {}), **state}; row.result_json = result; row.updated_at = utcnow(); row.heartbeat_at = utcnow()
            row.attempts = 0
            if done:
                row.status = "no_data" if row.kind == "telegram_replay" and result.get("outcome") == "no_eligible_signals" else "complete"
                row.progress = 1.0; row.completed_at = utcnow()
            else:
                row.status = "queued"; row.next_run_at = utcnow() + timedelta(seconds=1)
            db.commit(); db.refresh(row); return job_dict(row)
    except Exception as exc:
        with SessionLocal() as db:
            row = db.get(JobRecord, job_id)
            if row.status == 'cancelled':
                return job_dict(row)
            row.attempts = int(row.attempts or 0) + 1
            row.error = f"{type(exc).__name__}: {exc}"
            row.updated_at = utcnow(); row.heartbeat_at = utcnow()
            terminal = row.attempts >= row.max_attempts
            if terminal:
                row.status = "error"
            else:
                row.status = "retrying"; row.next_run_at = utcnow() + timedelta(seconds=_backoff(row.attempts))
            db.commit(); db.refresh(row); result = job_dict(row)
        if terminal:
            try:
                from app.services.notifications import notify_system_error
                await notify_system_error(f"Job #{job_id} ({result.get('kind')}) failed after {result.get('attempts')} attempts: {result.get('error')}")
            except Exception:
                pass
        return result
    finally:
        stop_heartbeat.set()
        await heartbeat_task


async def worker_once(limit: int = 3) -> int:
    now = utcnow()
    with SessionLocal() as db:
        # Recover jobs left running by a process restart.
        stale = db.query(JobRecord).filter(JobRecord.status == "running", JobRecord.heartbeat_at < now - timedelta(minutes=5)).all()
        for row in stale:
            row.status = "retrying"; row.next_run_at = now; row.error = "Recovered after stale worker heartbeat"
        db.commit()
        ids = [x.id for x in db.query(JobRecord).filter(JobRecord.status.in_(["queued", "retrying"]), JobRecord.next_run_at <= now).order_by(case((JobRecord.kind == "news_alerts", 0),
                (JobRecord.kind == "news_auto_paper", 1), else_=2),
                JobRecord.id.asc()).limit(limit).all()]
    for jid in ids:
        await run_one_job(jid)
    return len(ids)


def _seconds_until_next_job(max_wait: float = 1800.0) -> float:
    """Return how long the worker can sleep without touching Postgres.

    This is queried only when the queue is idle. New jobs wake the worker through
    JOB_WAKE_EVENT, so an idle Talos process no longer polls Neon every 3 seconds.
    """
    now = utcnow()
    with SessionLocal() as db:
        next_run = (
            db.query(JobRecord.next_run_at)
            .filter(JobRecord.status.in_(["queued", "retrying"]))
            .order_by(JobRecord.next_run_at.asc())
            .limit(1)
            .scalar()
        )
    if not next_run:
        return max_wait
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=timezone.utc)
    return max(0.0, min(max_wait, (next_run - now).total_seconds()))


async def worker_loop(stop_event: asyncio.Event | None = None) -> None:
    # Poll once at startup for durable recovery. After that, remain asleep in
    # process memory until a job is created/retried or a scheduled retry is due.
    while not (stop_event and stop_event.is_set()):
        JOB_WAKE_EVENT.clear()
        try:
            count = await worker_once()
        except Exception as exc:
            print(f"job worker cycle: {type(exc).__name__}: {exc}", flush=True)
            count = 0
        if count:
            await asyncio.sleep(0.2)
            continue
        try:
            delay = _seconds_until_next_job()
        except Exception:
            delay = 300.0
        if delay <= 0:
            await asyncio.sleep(0.2)
            continue
        await asyncio.to_thread(JOB_WAKE_EVENT.wait, delay)
