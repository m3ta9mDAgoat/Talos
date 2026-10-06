from __future__ import annotations
import asyncio
from app.db.session import SessionLocal
from app.models.source import SourceRecord
from app.models.research import StrategyDeploymentRecord
from app.services.universal_sources import sync_source
from app.services.processing import process_source
from app.services.deployments import tick

async def main():
    while True:
        with SessionLocal() as db:
            source_rows=db.query(SourceRecord).filter(SourceRecord.enabled==True).all(); sources=[(x.id,(x.config or {}).get('adapter') or str(getattr(x.type,'value',x.type))) for x in source_rows]
            deployments=[x.id for x in db.query(StrategyDeploymentRecord).filter(StrategyDeploymentRecord.enabled==True).all()]
        for sid,adapter in sources:
            try:
                await sync_source(sid)
                if adapter=='telegram': process_source(sid)
            except Exception as exc:
                print(f'source {sid}: {exc}')
        for did in deployments:
            try:
                await tick(did,confirm_live=False)
            except Exception as exc:
                print(f'deployment {did}: {exc}')
        await asyncio.sleep(60)

if __name__=='__main__': asyncio.run(main())
