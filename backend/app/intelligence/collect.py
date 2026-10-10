"""Bounded, idempotent, auditable OSINT collector for the existing Render + Neon deployment."""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
import re
from xml.etree import ElementTree as ET
import httpx
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from app.db.session import SessionLocal
from app.intelligence.classify import canonical_url, classify, clean_text, utc_date
from app.intelligence.sources import SOURCES, Source
from app.models.intelligence import IntelligenceEvent, IntelligenceIndicator, IntelligenceRevision, IntelligenceSignalLink, IntelligenceSourceState
from app.models.signal import SignalRecord

logger = logging.getLogger(__name__)
ATOM = "{http://www.w3.org/2005/Atom}"
MAX_SOURCE_BYTES = 2_000_000
MAX_EVENTS = 7500  # Stop adding before uncontrolled Neon storage growth; no silent archival deletion.
MAX_EVENT_AGE_DAYS = 30
USER_AGENT = "TalosResearch/4.5 (+public OSINT collector; no commercial redistribution)"


@dataclass
class Article:
    external_id: str
    title: str
    url: str
    summary: str = ""
    published_at: datetime | None = None


def _child_text(item, *names: str) -> str:
    for name in names:
        node = item.find(name)
        if node is not None:
            return "".join(node.itertext()).strip() if list(node) else (node.text or "").strip()
    return ""


def parse_feed(data: bytes) -> list[Article]:
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("RSS too large")
    # RSS is fetched from curated sources; defusedxml also blocks XML entities.
    from defusedxml.ElementTree import fromstring
    root = fromstring(data)
    items = root.findall(".//item") or root.findall(f".//{ATOM}entry")
    out: list[Article] = []
    for item in items[:45]:
        title = clean_text(_child_text(item, "title", f"{ATOM}title"), 750)
        link = _child_text(item, "link")
        if not link:
            link_node = item.find(f"{ATOM}link")
            if link_node is not None:
                link = link_node.get("href") or ""
        if not title or not link:
            continue
        try:
            link = canonical_url(link)
        except ValueError:
            continue
        external_id = _child_text(item, "guid", f"{ATOM}id") or link
        date = _child_text(item, "pubDate", f"{ATOM}published", f"{ATOM}updated", "{http://purl.org/dc/elements/1.1/}date")
        summary = _child_text(item, "description", f"{ATOM}summary", f"{ATOM}content")
        out.append(Article(external_id=external_id[:512], title=title, url=link, summary=clean_text(summary, 1600), published_at=utc_date(date)))
    return out


def parse_gdelt(data: dict) -> list[Article]:
    out = []
    for row in (data.get("articles") or [])[:35]:
        if not isinstance(row, dict):
            continue
        try:
            url = canonical_url(str(row.get("url", "")))
        except ValueError:
            continue
        title = clean_text(str(row.get("title") or ""), 750)
        if not title:
            continue
        raw_date = str(row.get("seendate") or "")
        published = None
        if raw_date:
            try:
                published = datetime.strptime(raw_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                published = utc_date(raw_date)
        out.append(Article(url, title, url, "", published))
    return out


def parse_github(data: list, repo: str) -> list[Article]:
    out = []
    for row in data[:5]:
        if not isinstance(row, dict) or row.get("draft"):
            continue
        try:
            url = canonical_url(row.get("html_url") or "")
        except ValueError:
            continue
        name = clean_text(str(row.get("name") or row.get("tag_name") or ""), 150)
        if not name:
            continue
        out.append(Article(str(row.get("id") or url), f"{repo}: {name} release", url,
                           clean_text(row.get("body") or "", 600), utc_date(row.get("published_at"))))
    return out


def parse_exchange_html(data: bytes, base_url: str) -> list[Article]:
    """Parse visible official announcement links; fail if publisher changes layout.

    HTML structure may be blocked or changed upstream. This is best-effort and
    the source status will say unavailable; never fabricate announcements.
    """
    soup = BeautifulSoup(data, "html.parser")
    host = urlsplit(base_url).hostname or ""
    seen = set()
    items = []
    for a in soup.select("a[href]"):
        title = clean_text(a.get_text(" ", strip=True), 220)
        url = urljoin(base_url, a.get("href") or "")
        parts = urlsplit(url)
        target_host = parts.hostname or ""
        if target_host != host:
            continue
        path = parts.path.lower()
        valid = (host.endswith("binance.com") and "/support/announcement/" in path and path.rstrip("/") != "/en/support/announcement") or \
                (host.endswith("okx.com") and "/help/" in path and "/category/" not in path and "/section/" not in path) or \
                (host.endswith("bybit.com") and ("/article/" in path or "/detail/" in path))
        if not valid or len(title) < 18 or len(title) > 220:
            continue
        if not re.search(r"(?i)\b(?:list|delist|trading|swap|migration|fork|network|maintenance|contract|update|halt|suspend|launch|withdraw|deposit|margin|future|security|risk)\b", title):
            continue
        try:
            url = canonical_url(url)
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        # A date embedded in a headline is the subject's date, not necessarily
        # the announcement publication timestamp. Leave published_at unknown.
        items.append(Article(url, title, url, "", None))
        if len(items) >= 30:
            break
    if not items:
        raise ValueError("No recognizable official announcements; source layout or access may have changed")
    return items


def fetch_source(source: Source, client: httpx.Client) -> tuple[list[Article], dict | None]:
    """Sources returning continuous measurements are never faked into articles."""
    if source.adapter == "gdelt":
        response = client.get("https://api.gdeltproject.org/api/v2/doc/doc", params={
            "query": source.endpoint, "mode": "ArtList", "format": "json", "maxrecords": "35", "timespan": "1d"})
    elif source.adapter == "github":
        response = client.get(f"https://api.github.com/repos/{source.endpoint}/releases", params={"per_page": "5"},
                              headers={"Accept": "application/vnd.github+json"})
    else:
        response = client.get(source.endpoint)
    response.raise_for_status()
    if len(response.content) > MAX_SOURCE_BYTES:
        raise ValueError("Source response size limit exceeded")
    if source.adapter == "rss":
        return parse_feed(response.content), None
    if source.adapter == "exchange_html":
        return parse_exchange_html(response.content, source.endpoint), None
    if source.adapter == "gdelt":
        return parse_gdelt(response.json()), None
    if source.adapter == "github":
        return parse_github(response.json(), source.endpoint), None
    if source.adapter == "mempool":
        payload = response.json()
        values = {k: float(payload[k]) for k in ("fastestFee", "halfHourFee", "hourFee", "economyFee", "minimumFee") if isinstance(payload.get(k), (float, int))}
        if not values:
            raise ValueError("No valid mempool metric supplied")
        return [], values
    raise ValueError(f"Unknown OSINT adapter: {source.adapter}")


def _utc(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def synchronize_signal_links(limit_events: int = 450, limit_signals: int = 220) -> int:
    """Bidirectional late-arrival matching: a signal or news item may arrive first.

    One bounded read batch per collector cycle; associations do not imply an edge.
    """
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        events = db.scalars(select(IntelligenceEvent).where(
            IntelligenceEvent.first_seen_at >= now - timedelta(days=7),
            IntelligenceEvent.symbols.is_not(None),
        ).order_by(IntelligenceEvent.id.desc()).limit(limit_events)).all()
        signals = db.scalars(select(SignalRecord).where(
            SignalRecord.timestamp >= now - timedelta(days=7),
        ).order_by(SignalRecord.id.desc()).limit(limit_signals)).all()
        if not events or not signals:
            return 0
        known = set(db.execute(select(IntelligenceSignalLink.event_id, IntelligenceSignalLink.signal_id)
                               .where(IntelligenceSignalLink.event_id.in_([e.id for e in events]))).all())
        count = 0
        for event in events:
            assets = {a.upper() for a in (event.symbols or [])}
            for signal in signals:
                if (event.id, signal.id) in known:
                    continue
                raw = (signal.symbol or "").upper().split(":")[-1].replace("/", "").replace("-", "")
                match = next((asset for asset in assets if raw in (asset, asset + "USDT", asset + "USD", asset + "USDC")), None)
                if match:
                    db.add(IntelligenceSignalLink(event_id=event.id, signal_id=signal.id, symbol=match))
                    known.add((event.id, signal.id))
                    count += 1
        if count:
            db.commit()
        return count


def store_articles(db, source: Source, articles: list[Article]) -> dict:
    now = datetime.now(timezone.utc)
    accepted, updated, ignored = 0, 0, 0
    # One indexed existing-event lookup per batch instead of repeated full table scans.
    candidates = {a.external_id[:512] for a in articles}
    existing = {e.external_id: e for e in db.scalars(select(IntelligenceEvent).where(
        IntelligenceEvent.source_key == source.key, IntelligenceEvent.external_id.in_(candidates))).all()} if candidates else {}
    stored_total = int(db.scalar(select(func.count(IntelligenceEvent.id))) or 0)
    for article in articles[:45]:
        published = _utc(article.published_at)
        if published and published > now + timedelta(hours=2):
            ignored += 1
            continue
        if published and published < now - timedelta(days=MAX_EVENT_AGE_DAYS):
            ignored += 1
            continue
        title = clean_text(article.title, 750)
        summary = clean_text(article.summary, 1600)
        if not title:
            ignored += 1
            continue
        content_hash = hashlib.sha256((title + "\0" + summary).encode()).hexdigest()
        key = article.external_id[:512]
        current = existing.get(key)
        if current:
            if current.content_hash == content_hash:
                ignored += 1
                continue  # Do not update last_seen_at on each fetch: avoids needless Neon writes.
            c = classify(title, summary, source.category, source.reliability)
            current.headline, current.summary, current.content_hash = title, summary, content_hash
            current.url, current.last_seen_at = article.url, now
            current.importance, current.classification_confidence = c["importance"], c["classification_confidence"]
            current.event_type, current.symbols, current.regions, current.cluster_key = c["event_type"], c["symbols"], c["regions"], c["cluster_key"]
            current.revision += 1
            db.add(IntelligenceRevision(event_id=current.id, revision=current.revision, observed_at=now,
                                        title=title, summary=summary, content_hash=content_hash))
            updated += 1
            continue
        if stored_total + accepted >= MAX_EVENTS:
            ignored += 1
            continue  # Hard stop; never run into unlimited growth on a free Neon DB.
        c = classify(title, summary, source.category, source.reliability)
        event = IntelligenceEvent(source_key=source.key, external_id=key, category=source.category,
                    event_type=c["event_type"], headline=title, summary=summary, url=article.url,
                    content_hash=content_hash, cluster_key=c["cluster_key"],
                    symbols=c["symbols"], regions=c["regions"], entities=c["entities"],
                    importance=c["importance"], classification_confidence=c["classification_confidence"],
                    source_reliability=source.reliability, published_at=published,
                    first_seen_at=now, last_seen_at=now, revision=1)
        db.add(event)
        db.flush()
        existing[key] = event
        db.add(IntelligenceRevision(event_id=event.id, revision=1, observed_at=now,
                                    title=title, summary=summary, content_hash=content_hash))
        accepted += 1
    return {"added": accepted, "revised": updated, "skipped": ignored}


def claim_source(db, source: Source, *, force: bool = False) -> bool:
    """Atomic lease works across multiple Render workers and restart boundaries."""
    now = datetime.now(timezone.utc)
    db.execute(pg_insert(IntelligenceSourceState).values(
        source_key=source.key, category=source.category, enabled=True, last_count=0,
    ).on_conflict_do_nothing(index_elements=["source_key"])) if db.bind.dialect.name == "postgresql" else None
    db.commit()
    state = db.get(IntelligenceSourceState, source.key)
    if not state:
        state = IntelligenceSourceState(source_key=source.key, category=source.category)
        db.add(state); db.commit()
    interval = timedelta(minutes=source.interval_minutes)
    clauses = [IntelligenceSourceState.source_key == source.key,
               IntelligenceSourceState.enabled.is_(True),
               or_(IntelligenceSourceState.lease_until.is_(None), IntelligenceSourceState.lease_until < now)]
    if not force:
        clauses.append(or_(IntelligenceSourceState.last_attempt_at.is_(None), IntelligenceSourceState.last_attempt_at < now - interval))
    result = db.execute(update(IntelligenceSourceState).where(and_(*clauses)).values(
        lease_until=now + timedelta(minutes=4), last_attempt_at=now).execution_options(synchronize_session=False))
    db.commit()
    return result.rowcount == 1


def collect_one(source: Source, client: httpx.Client | None = None, *, force: bool = False) -> dict:
    with SessionLocal() as db:
        if not claim_source(db, source, force=force):
            return {"source": source.key, "status": "not_due"}
    count = 0
    error = None
    result = {}
    try:
        if client is None:
            with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=9, follow_redirects=True) as owned:
                articles, indicators = fetch_source(source, owned)
        else:
            articles, indicators = fetch_source(source, client)
        count = len(articles) if indicators is None else len(indicators)
        with SessionLocal() as db:
            if indicators:
                existing = int(db.scalar(select(func.count(IntelligenceIndicator.id))) or 0)
                # Bounded history: stop writing indicators when limit reached, no silent purge.
                if existing + len(indicators) <= MAX_EVENTS:
                    for key, value in indicators.items():
                        db.add(IntelligenceIndicator(source_key=source.key, metric=key, asset="BTC", value=value))
            else:
                result = store_articles(db, source, articles)
            db.commit()
    except Exception as exc:
        # Never print auth token or URL query parameters into logs.
        error = f"{type(exc).__name__}: {str(exc)[:130]}"
        logger.warning("OSINT collector %s failed (%s)", source.key, type(exc).__name__)
    finally:
        with SessionLocal() as db:
            db.execute(update(IntelligenceSourceState).where(IntelligenceSourceState.source_key == source.key).values(
                lease_until=None,
                last_count=count if error is None else 0,
                last_error=error,
                **({"last_success_at": datetime.now(timezone.utc)} if error is None else {})))
            db.commit()
    # Dispatch important news processing as soon as this source finishes;
    # do not wait for all 23 network adapters (some of which may time out).
    if error is None and result.get("added", 0) > 0:
        try:
            from app.services.jobs import create_job
            from app.services.news_trading import config
            create_job("news_alerts", {}, max_attempts=3)
            if config()["auto_paper"]:
                create_job("news_auto_paper", {}, max_attempts=3)
        except Exception as notify_error:
            logger.warning("OSINT news job dispatch failed: %s", type(notify_error).__name__)
    return {"source": source.key, "status": "ok" if error is None else "error", "count": count,
            **result, **({"error": error} if error else {})}


def collect_due() -> list[dict]:
    outcomes = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=9, follow_redirects=True) as client:
        for source in SOURCES:
            outcomes.append(collect_one(source, client))
    try:
        linked = synchronize_signal_links()
        if linked:
            logger.info("Intelligence linked %d source events to recent signals", linked)
    except Exception:
        logger.exception("Intelligence signal-link maintenance failed")
    return outcomes


async def intelligence_loop():
    await asyncio.sleep(35)
    try:
        from app.intelligence.legacy import import_legacy
        await asyncio.to_thread(import_legacy)
    except Exception:
        logger.exception("Legacy OSINT import deferred; new collector continues")
    while True:
        try:
            await asyncio.to_thread(collect_due)
        except Exception:
            logger.exception("Intelligence collection cycle failed")
        await asyncio.sleep(60 * 60)  # Hourly prevents unnecessary Neon wake-ups on free tier.
