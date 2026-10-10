"""Read-only market intelligence + authenticated collector operations."""
from __future__ import annotations
from datetime import timedelta, datetime, timezone
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, func, and_, case, cast, Text
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.intelligence import IntelligenceEvent, IntelligenceRevision, IntelligenceSourceState, IntelligenceSignalLink
from app.models.signal import SignalRecord
from app.models.trading import PaperAccountRecord, PaperPositionRecord
from app.models.jobs import JobRecord
from app.services.intelligence import available_sources, now, collect_all, prune_old, point_in_time_features

router = APIRouter(prefix='/intelligence', tags=['Intelligence'])


def event_dict(r: IntelligenceEvent, links: int=0) -> dict:
    return {
        'id':r.id,'source':r.source_key,'category':r.category,'event_type':r.event_type,
        'title':r.headline,'summary':r.summary,'url':r.url,
        'importance':r.importance,'classification_confidence':r.classification_confidence,
        'source_reliability':r.source_reliability,
        'symbols':r.symbols,'entities':r.entities,'regions':r.regions,
        'published_at':r.published_at,'first_seen_at':r.first_seen_at,
        'last_seen_at':r.last_seen_at,'revision':r.revision,
        'linked_signal_count':links,
    }


@router.get('/feed')
def feed(
    category: str | None=Query(None, max_length=48),
    event_type: str | None=Query(None, max_length=48),
    symbol: str | None=Query(None, max_length=30),
    search: str | None=Query(None, max_length=100),
    hours: int=Query(72,ge=1,le=336),
    min_importance: float=Query(45,ge=0,le=100),
    limit: int=Query(20,ge=1,le=60),
    db: Session=Depends(get_db),
):
    """Bounded time/window and seek pagination. Scores are priority heuristics, not predictions."""
    since=now()-timedelta(hours=hours)
    cond=[IntelligenceEvent.first_seen_at >= since, IntelligenceEvent.importance >= min_importance]
    if category and category!='all': cond.append(IntelligenceEvent.category==category)
    if event_type and event_type!='all': cond.append(IntelligenceEvent.event_type==event_type)
    if search: cond.append(IntelligenceEvent.headline.ilike('%'+search.replace('%','\\%').replace('_','\\_')+'%',escape='\\'))
    if symbol: cond.append(cast(IntelligenceEvent.symbols, Text).like('%"'+symbol.upper().replace('%','').replace('_','')+'"%'))
    rows=list(db.scalars(select(IntelligenceEvent).where(*cond).order_by(IntelligenceEvent.id.desc()).limit(min(300,limit*6))))
    # Rank by significance but favor timeliness; suppress same-headline syndicated duplicates.
    seen=set(); result=[]
    counts={}
    if rows:
        counts=dict(db.execute(select(IntelligenceSignalLink.event_id,func.count(IntelligenceSignalLink.id)).where(IntelligenceSignalLink.event_id.in_([r.id for r in rows])).group_by(IntelligenceSignalLink.event_id)).all())
    for r in rows:
        if r.cluster_key in seen:
            continue
        seen.add(r.cluster_key)
        result.append(r)
    def _aware(value):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    result.sort(key=lambda r:(r.importance - min(26,max(0, (now()-_aware(r.first_seen_at)).total_seconds())/3600*0.8),r.first_seen_at),reverse=True)
    result=result[:limit]
    return {'items':[event_dict(r,counts.get(r.id,0)) for r in result], 'next_cursor':None,
            'scoring':'heuristic priority, not market-impact or outcome probability', 'as_of':now()}


@router.get('/events/{event_id}')
def event_detail(event_id:int, db:Session=Depends(get_db)):
    event=db.get(IntelligenceEvent,event_id)
    if not event: raise HTTPException(404,'Event not found')
    revisions=list(db.scalars(select(IntelligenceRevision).where(IntelligenceRevision.event_id==event_id).order_by(IntelligenceRevision.revision.asc())))
    links=list(db.execute(select(IntelligenceSignalLink.signal_id, IntelligenceSignalLink.symbol, IntelligenceSignalLink.link_reason).where(IntelligenceSignalLink.event_id==event_id).limit(100)).all())
    return {'event':event_dict(event,len(links)),
            'revisions':[{'revision':r.revision,'observed_at':r.observed_at,'title':r.title,'summary':r.summary,'content_hash':r.content_hash} for r in revisions],
            'signals':[{'id':i,'symbol':s,'reason':reason} for i,s,reason in links]}


@router.get('/sources')
def sources(db:Session=Depends(get_db)):
    states={s.source_key:s for s in db.scalars(select(IntelligenceSourceState)).all()}
    return [{'key':s.key,'name':s.title,'category':s.category,'kind':s.kind,
             'reliability_weight':s.reliability,'enabled':states[s.key].enabled if s.key in states else True,
             'last_attempt_at':states[s.key].last_attempt_at if s.key in states else None,
             'last_success_at':states[s.key].last_success_at if s.key in states else None,
             'last_count':states[s.key].last_count if s.key in states else 0,
             'last_error':states[s.key].last_error if s.key in states else None} for s in available_sources()]


@router.get('/summary')
def summary(db:Session=Depends(get_db)):
    cutoff=now()-timedelta(hours=24)
    events=db.execute(select(func.count(IntelligenceEvent.id),func.sum(case((IntelligenceEvent.importance >= 88,1),else_=0))).where(IntelligenceEvent.first_seen_at >= cutoff)).one()
    state=db.execute(select(func.count(IntelligenceSourceState.source_key),func.sum(case((IntelligenceSourceState.last_success_at >= now()-timedelta(hours=3),1),else_=0)))).one()
    last=db.scalar(select(func.max(IntelligenceSourceState.last_success_at)))
    return {'events_24h':events[0] or 0,'high_priority_24h':events[1] or 0,
            'configured_sources':len(available_sources()), 'initialized_sources':state[0] or 0,
            'recently_successful_sources':state[1] or 0,'last_ingested_at':last,
            'as_of':now()}


@router.get('/features')
def features(symbol:str=Query(...,min_length=2,max_length=20),
             hours:int=Query(24,ge=1,le=168),
             as_of:datetime|None=None,
             db:Session=Depends(get_db)):
    """Point-in-time as-observed OSINT indicators; safe for historical research queries."""
    observed_by=as_of or now()
    if observed_by.tzinfo is None:
        raise HTTPException(422,'as_of must include a timezone')
    observed_by=observed_by.astimezone(timezone.utc)
    if observed_by > now()+timedelta(minutes=1):
        raise HTTPException(422,'Future as_of not supported')
    result=point_in_time_features(db,symbol,observed_by,hours)
    result['caveat']='Research features only. Observed-at data is not predictive or automatically executable.'
    return result


@router.get('/dashboard')
def dashboard(db:Session=Depends(get_db)):
    """One inexpensive snapshot to replace browser-side N API queries."""
    cutoff=now()-timedelta(hours=24)
    paper=db.scalar(select(PaperAccountRecord).order_by(PaperAccountRecord.id.asc()).limit(1))
    open_positions=db.scalar(select(func.count()).select_from(PaperPositionRecord).where(PaperPositionRecord.status=='open')) or 0
    jobs=db.scalar(select(func.count()).select_from(JobRecord).where(JobRecord.status.in_(['queued','running','retrying']))) or 0
    signals=db.scalar(select(func.count()).select_from(SignalRecord).where(SignalRecord.timestamp >= cutoff)) or 0
    critical=db.scalar(select(func.count()).select_from(IntelligenceEvent).where(IntelligenceEvent.first_seen_at>=cutoff,IntelligenceEvent.importance>=88)) or 0
    last=db.scalar(select(func.max(IntelligenceSourceState.last_success_at)))
    failures=db.scalar(select(func.count()).select_from(IntelligenceSourceState).where(IntelligenceSourceState.last_error.isnot(None))) or 0
    return {'paper_equity':paper.equity if paper else None,'paper_initial_balance':paper.initial_balance if paper else None,
            'paper_realized_pnl':paper.realized_pnl if paper else None,
            'open_paper_positions':open_positions,'active_jobs':jobs,'signals_24h':signals,
            'high_priority_events_24h':critical, 'last_osint_sync':last,
            'osint_sources':len(available_sources()), 'source_errors':failures,'as_of':now()}


@router.post('/collect')
async def manual_collect(keys: list[str] | None=None):
    # Restricted by Talos existing admin session middleware.
    allowed={s.key for s in available_sources()}
    if keys and any(k not in allowed for k in keys):
        raise HTTPException(422,'Unknown source key')
    return await collect_all(keys=set(keys) if keys else None, force=True)


@router.post('/sources/{source_key}/enabled')
def toggle_source(source_key:str, enabled:bool, db:Session=Depends(get_db)):
    if source_key not in {s.key for s in available_sources()}:
        raise HTTPException(404,'Unknown source')
    state=db.get(IntelligenceSourceState,source_key)
    if state is None:
        src=next(s for s in available_sources() if s.key==source_key)
        state=IntelligenceSourceState(source_key=source_key,category=src.category)
        db.add(state)
    state.enabled=enabled
    db.commit()
    return {'source':source_key,'enabled':enabled}


@router.post('/prune')
def prune(days:int=Query(90,ge=30,le=365),db:Session=Depends(get_db)):
    n=prune_old(db,days)
    db.commit()
    return {'removed_unlinked_events':n,'retained_linked_audit':True}
