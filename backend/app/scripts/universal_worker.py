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

    try:
        await advance_live_simulations(limit=80)
    except Exception as exc:
        print(f"live simulation monitor: {exc}")

    for did in deployments:
        try:
            await tick(did, confirm_live=False)
        except Exception as exc:
            print(f"deployment {did}: {exc}")

    try:
        await send_daily_summary(force=False)
    except Exception as exc:
        print(f"daily summary: {exc}")


async def watcher_loop(stop_event: asyncio.Event | None = None) -> None:
    # Give the HTTP server time to become healthy before the first potentially
    # expensive source synchronization cycle.
    await asyncio.sleep(12)
    while not (stop_event and stop_event.is_set()):
        try:
            await run_cycle()
        except Exception as exc:
            print(f"watcher cycle: {exc}")
        await asyncio.sleep(60)


async def main():
    await watcher_loop()


if __name__ == "__main__":
    asyncio.run(main())
