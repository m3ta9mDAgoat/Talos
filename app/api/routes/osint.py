"""Read-only intelligence terminal and point-in-time research API."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from app.db.session import SessionLocal
from app.intelligence.classify import classify
from app.intelligence.sources import SOURCES, SOURCE_BY_KEY
from app.models.intelligence import IntelligenceEvent, IntelligenceIndicator, IntelligenceRevision, IntelligenceSignalLink, IntelligenceSourceState
from app.models.jobs import JobRecord
from app.models.signal import SignalRecord
from app.models.trading import PaperAccountRecord, PaperPositionRecord

router = APIRouter(prefix="/osint", tags=["OSINT intelligence"])


def _aware(dt):
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _rank(event, now):
    hours = max(0, (now - _aware(event.first_seen_at)).total_seconds() / 3600)
    freshness = 17 * (0.5 ** (hours / 18))
    return round(event.importance * .83 + freshness, 2)


def _row(event, now, links=0):
    source = SOURCE_BY_KEY.get(event.source_key)
    return {"id": event.id, "title": event.headline, "summary": event.summary,
            "source_key": event.source_key, "source": source.title if source else event.source_key,
            "category": event.category, "event_type": event.event_type, "url": event.url,
            "importance": event.importance, "rank": _rank(event, now),
            "classification_confidence": event.classification_confidence,
            "source_reliability": event.source_reliability, "symbols": event.symbols,
            "regions": event.regions, "published_at": event.published_at,
            "first_seen_at": event.first_seen_at, "last_seen_at": event.last_seen_at,
            "revision": event.revision, "linked_signals": links}


@router.get("/feed")
def feed(limit: int = Query(12, ge=1, le=100), category: str | None = None,
         source: str | None = None, important_only: bool = True,
         symbols: str | None = None, query: str | None = Query(None, max_length=100),
         window_hours: int = Query(168, ge=1, le=720), deduplicate: bool = True):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    with SessionLocal() as db:
        stmt = select(IntelligenceEvent).where(IntelligenceEvent.first_seen_at >= cutoff)
        if category and category != "all":
            stmt = stmt.where(IntelligenceEvent.category == category)
        if source:
            stmt = stmt.where(IntelligenceEvent.source_key == source)
        if important_only:
            stmt = stmt.where(IntelligenceEvent.importance >= 66)
        candidates = db.scalars(stmt.order_by(IntelligenceEvent.first_seen_at.desc()).limit(650)).all()
        if query:
            needle = query.casefold()
            candidates = [e for e in candidates if needle in e.headline.casefold() or needle in e.summary.casefold()]
        if symbols:
            ticker = symbols.upper().strip()
            candidates = [e for e in candidates if ticker in (e.symbols or [])]
        candidates.sort(key=lambda e: (_rank(e, now), _aware(e.first_seen_at)), reverse=True)
        if deduplicate:
            seen = set()
            filtered = []
            for row in candidates:
                if row.cluster_key in seen:
                    continue
                seen.add(row.cluster_key)
                filtered.append(row)
            candidates = filtered
        selected = candidates[:limit]
        if not selected:
            return []
        links = dict(db.execute(select(IntelligenceSignalLink.event_id, func.count(IntelligenceSignalLink.id))
                                .where(IntelligenceSignalLink.event_id.in_([e.id for e in selected]))
                                .group_by(IntelligenceSignalLink.event_id)).all())
        return [_row(e, now, links.get(e.id, 0)) for e in selected]


@router.get("/events/{event_id}")
def event_detail(event_id: int):
    with SessionLocal() as db:
        event = db.get(IntelligenceEvent, event_id)
        if not event:
            raise HTTPException(404, "Event not found")
        revisions = db.scalars(select(IntelligenceRevision).where(IntelligenceRevision.event_id == event_id)
                               .order_by(IntelligenceRevision.revision.asc()).limit(80)).all()
        links = db.execute(select(IntelligenceSignalLink.signal_id, IntelligenceSignalLink.symbol,
                                  IntelligenceSignalLink.link_reason).where(
                                      IntelligenceSignalLink.event_id == event_id).limit(100)).all()
        return {**_row(event, datetime.now(timezone.utc), len(links)),
                "observations": [{"version": x.revision, "observed_at": x.observed_at, "title": x.title,
                                  "summary": x.summary, "content_hash": x.content_hash} for x in revisions],
                "related_signals": [{"signal_id": x[0], "symbol": x[1], "reason": x[2]} for x in links],
                "note": "An association is not evidence of causality or a recommendation to trade."}


@router.get("/sources")
def sources():
    with SessionLocal() as db:
        rows = {r.source_key: r for r in db.scalars(select(IntelligenceSourceState)).all()}
        return [{"key": s.key, "name": s.title, "category": s.category, "adapter": s.adapter,
                 "interval_minutes": s.interval_minutes, "enabled": rows[s.key].enabled if s.key in rows else True,
                 "status": ("unavailable" if rows[s.key].last_error else "healthy") if s.key in rows and rows[s.key].last_attempt_at else "awaiting_first_check",
                 "last_checked": rows[s.key].last_attempt_at if s.key in rows else None,
                 "last_success": rows[s.key].last_success_at if s.key in rows else None,
                 "last_count": rows[s.key].last_count if s.key in rows else 0,
                 "last_error": rows[s.key].last_error if s.key in rows else None} for s in SOURCES]


@router.get("/dashboard")
def dashboard():
    """One lightweight dashboard query batch. No DB storage-size scan or network fetch."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        account = db.scalar(select(PaperAccountRecord).order_by(PaperAccountRecord.id.asc()).limit(1))
        active_jobs = int(db.scalar(select(func.count(JobRecord.id)).where(
            JobRecord.status.in_(["queued", "running", "retrying"]))) or 0)
        open_positions = int(db.scalar(select(func.count(PaperPositionRecord.id)).where(
            PaperPositionRecord.status == "open")) or 0)
        signals_today = int(db.scalar(select(func.count(SignalRecord.id)).where(
            SignalRecord.created_at >= now - timedelta(hours=24))) or 0)
        osint_24h = int(db.scalar(select(func.count(IntelligenceEvent.id)).where(
            IntelligenceEvent.first_seen_at >= now - timedelta(hours=24))) or 0)
        critical_24h = int(db.scalar(select(func.count(IntelligenceEvent.id)).where(
            IntelligenceEvent.first_seen_at >= now - timedelta(hours=24), IntelligenceEvent.importance >= 87)) or 0)
        rows = db.scalars(select(IntelligenceSourceState)).all()
        ready = sum(1 for r in rows if r.enabled and r.last_success_at and not r.last_error)
        failed = sum(1 for r in rows if r.enabled and r.last_error)
        return {"as_of": now, "paper_equity": account.equity if account else None,
                "paper_realized_pnl": account.realized_pnl if account else None,
                "active_jobs": active_jobs, "open_paper_positions": open_positions,
                "new_signals_24h": signals_today, "osint_events_24h": osint_24h,
                "critical_events_24h": critical_24h, "healthy_osint_sources": ready,
                "failed_osint_sources": failed, "total_osint_sources": len(SOURCES)}


@router.get("/indicators")
def indicators(limit: int = Query(25, ge=1, le=100)):
    with SessionLocal() as db:
        entries = db.scalars(select(IntelligenceIndicator).order_by(IntelligenceIndicator.observed_at.desc())
                             .limit(min(800, limit * 15))).all()
        used = set()
        result = []
        for entry in entries:
            key = (entry.source_key, entry.metric)
            if key in used:
                continue
            used.add(key)
            result.append({"source": entry.source_key, "metric": entry.metric, "asset": entry.asset,
                           "value": entry.value, "observed_at": entry.observed_at,
                           "unit": "sat/vB" if entry.source_key == "btc_network" else None})
            if len(result) >= limit:
                break
        return result


@router.get("/features")
def features(as_of: datetime | None = None, lookback_hours: int = Query(24, ge=1, le=720),
             symbol: str | None = Query(None, max_length=20), limit: int = Query(300, ge=1, le=500)):
    """Replay-safe categorical OSINT observations available strictly by first_seen_at.

    An edited headline is classified as it was known *as_of*, not using its later revision.
    This endpoint deliberately makes no prediction or trading decision.
    """
    now = datetime.now(timezone.utc)
    point = _aware(as_of) if as_of else now
    if point > now:
        raise HTTPException(422, "as_of cannot be in the future")
    with SessionLocal() as db:
        events = db.scalars(select(IntelligenceEvent).where(
            IntelligenceEvent.first_seen_at <= point,
            IntelligenceEvent.first_seen_at >= point - timedelta(hours=lookback_hours),
        ).order_by(IntelligenceEvent.first_seen_at.desc()).limit(limit)).all()
        if not events:
            return {"as_of": point, "lookback_hours": lookback_hours, "features": []}
        revisions = db.scalars(select(IntelligenceRevision).where(
            IntelligenceRevision.event_id.in_([e.id for e in events]),
            IntelligenceRevision.observed_at <= point).order_by(
                IntelligenceRevision.event_id, IntelligenceRevision.revision)).all()
        latest = {r.event_id: r for r in revisions}
        rows = []
        for e in events:
            r = latest.get(e.id)
            if not r:
                continue  # Never infer state from a future revision.
            props = classify(r.title, r.summary, e.category, e.source_reliability)
            if symbol and symbol.upper() not in props["symbols"]:
                continue
            rows.append({"event_id": e.id, "first_seen_at": e.first_seen_at,
                         "observed_at": r.observed_at, "revision": r.revision,
                         "source": e.source_key, "event_type": props["event_type"],
                         "importance": props["importance"], "symbols": props["symbols"],
                         "regions": props["regions"], "source_reliability": e.source_reliability,
                         "classification_confidence": props["classification_confidence"]})
        return {"as_of": point, "lookback_hours": lookback_hours, "features": rows,
                "warning": "Features describe observed public information, not proven predictive power."}
