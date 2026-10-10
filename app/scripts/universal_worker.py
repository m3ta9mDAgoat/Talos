from __future__ import annotations

import asyncio

from sqlalchemy import func

from app.db.session import SessionLocal
from app.models.raw_event import RawEventRecord
from app.models.research import StrategyDeploymentRecord
from app.models.signal import SignalRecord
from app.models.source import SourceRecord
from app.services.deployments import tick
from app.services.live_signal_monitor import advance_live_simulations, ensure_live_simulation
from app.services.runtime_wake import SIM_WAKE_EVENT
from app.services.notifications import notify_signal, notify_system_error, send_daily_summary
from app.services.processing import process_source
from app.services.universal_sources import sync_source


async def _monitor_telegram_source(source_id: int) -> None:
    # Notifications are incremental. On the first watcher cycle we establish a
    # baseline from the existing archive so historical imports never spam the bot.
    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            return
        cfg = dict(source.config or {})
        initialized = bool(cfg.get("notification_initialized", False))
        current_signal_max = db.query(func.max(SignalRecord.id)).filter(SignalRecord.source_id == source.identifier).scalar() or 0
        signal_cursor = int(cfg.get("notify_signal_cursor", current_signal_max))

    await sync_source(source_id)
    # Classification/parsing may walk thousands of archived messages. Keep it
    # off this runtime's asyncio loop as well.
    processed = await asyncio.to_thread(process_source, source_id)

    with SessionLocal() as db:
        source = db.get(SourceRecord, source_id)
        if not source:
            return
        max_signal = db.query(func.max(SignalRecord.id)).filter(SignalRecord.source_id == source.identifier).scalar() or signal_cursor
        cfg = dict(source.config or {})
        cfg["notify_signal_cursor"] = int(max_signal)
        cfg["notification_initialized"] = True
        source.config = cfg
        db.commit()

    if not initialized:
        # Existing history is now the baseline. Future cycles are live-only.
        return

    new_signal_ids = [int(x) for x in (processed.get("created_signal_ids") or []) if int(x) > signal_cursor]
    edited_signal_ids = [int(x) for x in (processed.get("updated_signal_ids") or []) if int(x) not in set(new_signal_ids)]

    for signal_id in new_signal_ids:
        ensure_live_simulation(signal_id, source_id)
        try:
            await notify_signal(signal_id, "new")
        except Exception as exc:
            print(f"notify new signal {signal_id}: {exc}")

    for signal_id in sorted(set(edited_signal_ids)):
        ensure_live_simulation(signal_id, source_id)
        try:
            await notify_signal(signal_id, "edit")
        except Exception as exc:
            print(f"notify edited signal {signal_id}: {exc}")


async def run_cycle() -> None:
    with SessionLocal() as db:
        source_rows = db.query(SourceRecord).filter(SourceRecord.enabled == True).all()
        sources = [(x.id, (x.config or {}).get("adapter") or str(getattr(x.type, "value", x.type))) for x in source_rows]
        deployments = [x.id for x in db.query(StrategyDeploymentRecord).filter(StrategyDeploymentRecord.enabled == True).all()]

    for sid, adapter in sources:
        try:
            if adapter == "telegram":
                await _monitor_telegram_source(sid)
            else:
                await sync_source(sid)
        except Exception as exc:
            print(f"source {sid}: {exc}")

    for did in deployments:
        try:
            await tick(did, confirm_live=False)
        except Exception as exc:
            print(f"deployment {did}: {exc}")

    try:
        await send_daily_summary(force=False)
    except Exception as exc:
        print(f"daily summary: {exc}")


async def _periodic_catchup_loop(stop_event: asyncio.Event | None = None) -> None:
    # Telegram's event stream is the real-time path. This is only a safety pass
    # for missed events/non-Telegram sources, so it must not keep Neon awake.
    await asyncio.sleep(60)
    while not (stop_event and stop_event.is_set()):
        try:
            await run_cycle()
        except Exception as exc:
            print(f"watcher catch-up cycle: {exc}", flush=True)
        await asyncio.sleep(1800)


async def _simulation_monitor_loop(stop_event: asyncio.Event | None = None) -> None:
    # Live simulations need minute-level market checks only while a simulation is
    # active. With no active trades, wait in process memory (or until a new signal
    # wakes us) instead of querying Postgres every minute.
    while not (stop_event and stop_event.is_set()):
        SIM_WAKE_EVENT.clear()
        try:
            result = await advance_live_simulations(limit=80)
            checked = int(result.get("checked", 0))
        except Exception as exc:
            print(f"live simulation monitor: {type(exc).__name__}: {exc}", flush=True)
            checked = 0
        if checked:
            await asyncio.sleep(60)
        else:
            await asyncio.to_thread(SIM_WAKE_EVENT.wait, 3600.0)


async def watcher_loop(stop_event: asyncio.Event | None = None) -> None:
    from app.services.telegram_live_watch import stream_supervisor
    await asyncio.gather(
        _periodic_catchup_loop(stop_event),
        _simulation_monitor_loop(stop_event),
        stream_supervisor(stop_event),
    )


async def main():
    await watcher_loop()


if __name__ == "__main__":
    asyncio.run(main())
