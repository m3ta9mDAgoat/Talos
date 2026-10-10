"""Conservative public RSS ingestion; no invented sentiment or trading signals."""
import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.osint import OsintItem

log = logging.getLogger(__name__)
# Fixed allowlist; no user-supplied URLs or arbitrary network fetches.
FEEDS = (
    ('SEC Press Releases', 'regulation', 'https://www.sec.gov/news/pressreleases.rss'),
    ('Federal Reserve', 'macro', 'https://www.federalreserve.gov/feeds/press_all.xml'),
    ('CISA Advisories', 'cybersecurity', 'https://www.cisa.gov/cybersecurity-advisories/all.xml'),
    ('BBC World', 'geopolitics', 'https://feeds.bbci.co.uk/news/world/rss.xml'),
    ('BBC Business', 'business', 'https://feeds.bbci.co.uk/news/business/rss.xml'),
)
KEYWORDS = {'delist': 95, 'exploit': 94, 'breach': 90, 'sanction': 90, 'emergency': 89, 'interest rate': 88,
            'inflation': 85, 'cyberattack': 90, 'lawsuit': 78, 'acquisition': 75, 'earnings': 72,
            'listing': 70, 'outage': 77, 'oil': 69, 'tariff': 78, 'security': 68}

def _read_feed(source, category, url):
    req = Request(url, headers={'User-Agent': 'TalosResearch/4.4 (public feed reader)', 'Accept': 'application/rss+xml, application/atom+xml, application/xml'})
    with urlopen(req, timeout=12) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError('Feed exceeds size limit')
    root = ET.fromstring(data)
    entries = root.findall('.//item') or root.findall('.//{http://www.w3.org/2005/Atom}entry')
    rows = []
    for entry in entries[:35]:
        def field(name):
            node = entry.find(name)
            return (node.text or '').strip() if node is not None else ''
        title = field('title') or field('{http://www.w3.org/2005/Atom}title')
        link = field('link')
        if not link:
            node = entry.find('{http://www.w3.org/2005/Atom}link')
            link = node.get('href', '') if node is not None else ''
        if not title or not link.startswith(('https://', 'http://')):
            continue
        date = field('pubDate') or field('{http://www.w3.org/2005/Atom}published')
        published = None
        if date:
            try:
                published = parsedate_to_datetime(date) if ',' in date else datetime.fromisoformat(date.replace('Z','+00:00'))
                if published.tzinfo is None: published = published.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError): pass
        summary = field('description') or field('{http://www.w3.org/2005/Atom}summary')
        score = max((v for k,v in KEYWORDS.items() if k in title.lower()), default=45)
        rows.append(dict(source=source, category=category, title=title[:1000], url=link[:2000],
                         fingerprint=hashlib.sha256((source+'\0'+link).encode()).hexdigest(),
                         published_at=published, importance=float(score), summary=summary[:1500]))
    return rows

def collect_once():
    counts = {}
    for source, category, url in FEEDS:
        try:
            rows = _read_feed(source, category, url)
            with SessionLocal() as db:
                for row in rows:
                    if db.bind.dialect.name == 'postgresql':
                        db.execute(pg_insert(OsintItem).values(**row).on_conflict_do_nothing(index_elements=['fingerprint']))
                    elif not db.scalar(select(OsintItem.id).where(OsintItem.fingerprint == row['fingerprint'])):
                        db.add(OsintItem(**row))
                db.commit()
            counts[source] = len(rows)
        except Exception as exc:
            log.warning('OSINT feed %s failed: %s', source, exc)
            counts[source] = 'unavailable'
    return counts

async def collector_loop():
    await asyncio.sleep(45)
    while True:
        await asyncio.to_thread(collect_once)
        await asyncio.sleep(1800)
