"""Always-on Telegram incremental worker for VPS/local deployment.

Render Free sleeps and is not appropriate for guaranteed 24/7 listeners. Run this
process beside the API on an always-on host. It archives each new/edit event and
runs the incremental classifier/parser, which skips every cached historical row.
"""
from __future__ import annotations
import asyncio
from app.db.session import SessionLocal
from app.models.source import SourceRecord
from app.plugins.datasources.telegram import TelegramSource
from app.services.ingestion import persist_events
from app.services.processing import process_source

async def watch(source: SourceRecord):
    connector=TelegramSource()
    async for event in connector.stream(channel=source.identifier):
        with SessionLocal() as db:
            persist_events(db, source.identifier, [event])
        await asyncio.to_thread(process_source, source.id, force=False)

async def main():
    with SessionLocal() as db:
        sources=[s for s in db.query(SourceRecord).filter(SourceRecord.enabled.is_(True)).all() if str(s.type)=="telegram"]
    if not sources:
        print("No enabled Telegram sources.")
        return
    await asyncio.gather(*(watch(s) for s in sources))

if __name__=="__main__":
    asyncio.run(main())
