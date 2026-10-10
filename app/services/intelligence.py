"""Market-wide, evidence-first OSINT ingestion for the existing Render + Neon stack.

All input is PUBLIC and allowlisted. Classifications are deterministic heuristics, NOT
predictions, truth assessments, or authorization to place orders.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl, urljoin
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select, update, func, delete, and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.intelligence import (
    IntelligenceEvent, IntelligenceRevision, IntelligenceSourceState,
    IntelligenceSignalLink,
)
from app.models.signal import SignalRecord

log = logging.getLogger(__name__)
UTC = timezone.utc
# Used for gentle polling rather than persistent connections; hard limits avoid runaway Neon/network usage.
MAX_BODY = 1_200_000
MAX_ITEMS = 25
MAX_EVENT_AGE_DAYS = 14
MAX_LINK_AGE_HOURS = 72
FETCH_TIMEOUT = 12.0
INTERVAL_SECONDS = max(900, int(os.getenv('OSINT_INTERVAL_SECONDS', '1800')))
RETENTION_DAYS = max(7, int(os.getenv('OSINT_RETENTION_DAYS', '90')))
OSINT_MAX_DB_BYTES = max(100_000_000, int(os.getenv('OSINT_MAX_DB_BYTES', '650000000')))
_last_storage_check = None
_storage_ok = True
FEED_USER_AGENT = 'Talos-OSINT/4.4 (+public-market-research; contact configured by operator)'

@dataclass(frozen=True)
class Source:
    key: str
    title: str
    category: str
    kind: str
    endpoint: str
    reliability: float = .85
    interval: int = 3600

# Approved public sources. A source may be unavailable from Render; this is reported, not fabricated.
SOURCES: tuple[Source, ...] = (
    Source('sec', 'US SEC · press releases', 'stocks', 'rss', 'https://www.sec.gov/news/pressreleases.rss', .98, 3600),
    Source('fed', 'Federal Reserve', 'macro', 'rss', 'https://www.federalreserve.gov/feeds/press_all.xml', .99, 3600),
    Source('fed_speeches', 'Federal Reserve · speeches', 'macro', 'rss', 'https://www.federalreserve.gov/feeds/speeches.xml', .99, 7200),
    Source('ecb', 'European Central Bank', 'forex', 'rss', 'https://mid.ecb.europa.eu/rss/mid.xml', .99, 3600),
    Source('eia', 'US Energy Information Administration', 'commodities', 'rss', 'https://www.eia.gov/rss/todayinenergy.xml', .97, 7200),
    Source('cisa', 'CISA cybersecurity advisories', 'cybersecurity', 'rss', 'https://www.cisa.gov/cybersecurity-advisories/all.xml', .97, 7200),
    Source('bbc_world', 'BBC · world', 'geopolitics', 'rss', 'https://feeds.bbci.co.uk/news/world/rss.xml', .86, 3600),
    Source('bbc_business', 'BBC · business', 'stocks', 'rss', 'https://feeds.bbci.co.uk/news/business/rss.xml', .86, 3600),
    Source('bbc_tech', 'BBC · technology', 'technology', 'rss', 'https://feeds.bbci.co.uk/news/technology/rss.xml', .86, 7200),
    Source('bbc_science', 'BBC · science', 'technology', 'rss', 'https://feeds.bbci.co.uk/news/science_and_environment/rss.xml', .86, 7200),
    Source('binance_announce', 'Binance · official announcements', 'crypto', 'binance_html', 'https://www.binance.com/en/support/announcement/', .97, 3600),
    Source('kraken_blog', 'Kraken · official blog', 'crypto', 'rss', 'https://blog.kraken.com/feed', .94, 3600),
    Source('coinbase_status', 'Coinbase · service status', 'crypto', 'rss', 'https://status.coinbase.com/history.rss', .98, 3600),
    Source('gdelt_markets', 'GDELT · global market events', 'global', 'gdelt', 'https://api.gdeltproject.org/api/v2/doc/doc', .70, 3600),
    Source('gdelt_energy', 'GDELT · energy & supply', 'commodities', 'gdelt', 'https://api.gdeltproject.org/api/v2/doc/doc', .70, 3600),
    Source('gdelt_cyber', 'GDELT · cyber incidents', 'cybersecurity', 'gdelt', 'https://api.gdeltproject.org/api/v2/doc/doc', .70, 3600),
)
GDELT_QUERIES = {
    'gdelt_markets': '(central bank OR interest rate OR inflation OR tariff OR sanctions)',
    'gdelt_energy': '(oil supply disruption OR refinery explosion OR OPEC OR shipping disruption)',
    'gdelt_cyber': '(cryptocurrency exchange hacked OR ransomware attack OR major data breach)',
}
GITHUB_REPOS = tuple(r.strip() for r in os.getenv('OSINT_GITHUB_REPOS', 'bitcoin/bitcoin,ethereum/go-ethereum,solana-labs/solana').split(',') if re.fullmatch(r'[\w.-]+/[\w.-]+', r.strip()))[:12]

def available_sources() -> tuple[Source, ...]:
    ciks=tuple(re.sub(r'\D', '', x) for x in os.getenv('OSINT_SEC_CIKS', '').split(',') if re.fullmatch(r'\d{1,10}',x.strip()))[:8]
    sec=tuple(Source('edgar_'+cik, 'SEC EDGAR · CIK '+cik, 'stocks', 'sec_submissions', 'https://data.sec.gov/submissions/CIK'+cik.zfill(10)+'.json', .99, 7200) for cik in ciks if os.getenv('OSINT_CONTACT_EMAIL', '').strip())
    return SOURCES + sec + tuple(Source('github_' + r.replace('/', '__'), 'GitHub · ' + r, 'technology', 'github', 'https://api.github.com/repos/' + r + '/releases', .92, 7200) for r in GITHUB_REPOS)


def now() -> datetime:
    return datetime.now(UTC)


def clean(s: Any, limit=1000) -> str:
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', str(s or '')))).strip()[:limit]


def parsed_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s) if ',' in s else datetime.fromisoformat(s.replace('Z', '+00:00'))
        return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        return None


def normalize_url(url: str) -> str:
    u = urlsplit(url.strip())
    if u.scheme not in {'https', 'http'} or not u.netloc or u.username or u.password:
        return ''
    if u.hostname in {'localhost', '127.0.0.1', '0.0.0.0'}:
        return ''
    params = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if not k.lower().startswith(('utm_', 'mc_', 'ref_')) and k.lower() not in {'fbclid', 'gclid'}]
    return urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path.rstrip('/') or '/', urlencode(params), ''))[:2000]


def extract_xml(data: bytes) -> list[dict]:
    """Minimal RSS/Atom reader, no external XML entities or dynamic network resolution."""
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('DTD/entity expansion not supported')
    root = ElementTree.fromstring(data)
    atom = '{http://www.w3.org/2005/Atom}'
    dc = '{http://purl.org/dc/elements/1.1/}'
    rows = []
    entries = root.findall('.//item') or root.findall('.//' + atom + 'entry')
    for node in entries[:MAX_ITEMS]:
        def field(*keys):
            for key in keys:
                e = node.find(key)
                if e is not None and e.text:
                    return e.text
            return ''
        title = clean(field('title', atom+'title'), 750)
        link = field('link')
        if not link:
            for e in node.findall(atom+'link'):
                if e.get('rel', 'alternate') == 'alternate' and e.get('href'):
                    link = e.get('href', '')
                    break
        url = normalize_url(link)
        if not title or not url:
            continue
        rows.append({
            'external_id': clean(field('guid', atom+'id'), 500) or url,
            'headline': title,
            'summary': clean(field('description', atom+'summary', atom+'content'), 1200),
            'url': url,
            'published_at': parsed_date(field('pubDate', atom+'published', atom+'updated', dc+'date')),
        })
    return rows


def extract_gdelt(obj: dict) -> list[dict]:
    rows = []
    for a in obj.get('articles', [])[:MAX_ITEMS]:
        url = normalize_url(a.get('url', ''))
        headline = clean(a.get('title'), 750)
        if url and headline:
            rows.append({'external_id': url, 'headline': headline, 'summary': '', 'url': url,
                         'published_at': parsed_date(a.get('seendate')) or parsed_date((a.get('seendate') or '').replace('T', ''))})
            # GDELT dates are often YYYYMMDDTHHMMSSZ.
            stamp = a.get('seendate', '')
            try:
                rows[-1]['published_at'] = datetime.strptime(stamp, '%Y%m%dT%H%M%SZ').replace(tzinfo=UTC)
            except (ValueError, TypeError):
                pass
    return rows


def extract_binance_html(data: bytes) -> list[dict]:
    """Best-effort official page parser; empty page yields zero events (never imagined news)."""
    doc=BeautifulSoup(data, 'html.parser')
    rows=[]; seen=set()
    for a in doc.select('a[href]'):
        href=a.get('href','')
        if not re.search(r'/support/announcement/detail/[a-zA-Z0-9-]+',href):
            continue
        title=clean(a.get_text(' ',strip=True),750)
        url=normalize_url(urljoin('https://www.binance.com',href))
        if len(title)<15 or not url or url in seen:
            continue
        seen.add(url)
        rows.append({'external_id':url,'headline':title,'summary':'', 'url':url,'published_at':None})
        if len(rows)>=MAX_ITEMS:
            break
    return rows


def extract_sec_submissions(obj: dict) -> list[dict]:
    """EDGAR latest submissions, optional watchlisted CIKs; no unauthenticated bulk crawling."""
    recent=((obj.get('filings') or {}).get('recent') or {})
    forms=recent.get('form') or []
    accessions=recent.get('accessionNumber') or []
    dates=recent.get('filingDate') or []
    documents=recent.get('primaryDocument') or []
    company=clean(obj.get('name') or ('CIK '+str(obj.get('cik',''))),250)
    cik=str(obj.get('cik','')).lstrip('0')
    rows=[]
    for i, form in enumerate(forms[:MAX_ITEMS]):
        if not cik or i>=len(accessions) or i>=len(dates) or i>=len(documents):
            continue
        accession=str(accessions[i])
        doc=str(documents[i])
        if not re.fullmatch(r'[0-9-]+',accession) or '/' in doc or '..' in doc:
            continue
        url=normalize_url(f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace("-", "")}/{doc}')
        if not url:
            continue
        rows.append({'external_id':accession,'headline':f'{company} filed {clean(form,25)} with the SEC',
                     'summary':f'Official SEC filing · {company} · form {clean(form,25)}',
                     'url':url, 'published_at':parsed_date(str(dates[i])+'T00:00:00+00:00')})
    return rows


def extract_github(obj: Any) -> list[dict]:
    rows = []
    if not isinstance(obj, list):
        return rows
    for r in obj[:MAX_ITEMS]:
        if not isinstance(r, dict):
            continue
        url = normalize_url(r.get('html_url', ''))
        title = clean((r.get('name') or r.get('tag_name') or '') + (' · prerelease' if r.get('prerelease') else ''), 750)
        if url and title:
            rows.append({'external_id': str(r.get('id') or url), 'headline': title,
                         'summary': clean(r.get('body'), 1200), 'url': url,
                         'published_at': parsed_date(r.get('published_at') or r.get('created_at'))})
    return rows

# Rule precedence is deliberate: e.g. 'delisting' must beat 'listing'.
RULES = (
    ('delisting', 99, (r'\bdelist(?:ing|ed|s)?\b', r'\btrading suspension\b')),
    ('exploit', 98, (r'\bexploit(?:ed)?\b', r'\bhack(?:ed)?\b', r'\bsmart contract attack\b', r'\bsecurity breach\b')),
    ('sanctions', 94, (r'\bsanction(?:s|ed)?\b', r'\bembargo\b')),
    ('outage', 91, (r'\boutage\b', r'\bservice disruption\b', r'\btrading halt\b')),
    ('regulation', 89, (r'\bsec (?:charges|sues|approves|rejects)\b', r'\bregulat(?:or|ion|ory)\b', r'\blawsuit\b', r'\bantitrust\b')),
    ('monetary_policy', 90, (r'\binterest rates?\b', r'\bfederal reserve\b', r'\bfomc\b', r'\brate cut\b', r'\brate hike\b')),
    ('conflict', 90, (r'\bwar\b', r'\bmissile\b', r'\bairstrike\b', r'\bmilitary strike\b', r'\binvasion\b')),
    ('supply_shock', 88, (r'\bsupply disruption\b', r'\bpipeline shutdown\b', r'\bshipping disruption\b', r'\bopec\b')),
    ('merger', 82, (r'\bacquisition\b', r'\bmerger\b', r'\btakeover\b')),
    ('earnings', 78, (r'\bearnings\b', r'\bquarterly results\b', r'\bguidance\b')),
    ('filing', 77, (r'\bfiled (?:8-k|10-k|10-q|s-1|form)\b', r'\bfiled annual report\b')), 
    ('listing', 79, (r'\blist(?:ing|ed)?\b', r'\blaunch(?:es|ed)? trading\b')),
    ('inflation', 82, (r'\binflation\b', r'\bconsumer price index\b', r'\bcpi\b')),
    ('security_advisory', 74, (r'\bcve-\d{4}-\d+\b', r'\bvulnerability\b', r'\bransomware\b')),
    ('release', 62, (r'\bnew release\b', r'\bversion \d', r'\bsoftware update\b')),
)
# Only explicit unambiguous names / cashtags count. No guessing from all-capital common words.
KNOWN = {
    'bitcoin': 'BTC', 'btc': 'BTC', 'ethereum': 'ETH', 'ether': 'ETH', 'solana': 'SOL',
    'cardano': 'ADA', 'ripple': 'XRP', 'dogecoin': 'DOGE', 'gold': 'XAU',
    'silver': 'XAG', 'crude oil': 'WTI', 'brent': 'BRENT', 'natural gas': 'NG',
    'nasdaq': 'NDX', 's&p 500': 'SPX', 'dow jones': 'DJI',
    'usd/jpy': 'USDJPY', 'eur/usd': 'EURUSD', 'gbp/usd': 'GBPUSD',
}


def classify(headline: str, summary: str, source: Source) -> dict:
    text = (headline + ' ' + summary[:300]).lower()
    found = [(event, weight) for event, weight, patterns in RULES if any(re.search(p, text) for p in patterns)]
    typ, base = max(found, key=lambda p:p[1]) if found else (('release',62) if source.kind=='github' else ('report',42))
    symbols = sorted({v for k,v in KNOWN.items() if re.search(r'(?<![\w])'+re.escape(k)+r'(?![\w])', text)})
    symbols.extend(s for s in re.findall(r'(?<!\w)\$([A-Z]{2,8})(?!\w)', headline) if s not in symbols)
    for token in re.findall(r'(?i)\b(?:delist(?:ing)?|list(?:ing)?|remove|add)\s+([A-Z][A-Z0-9]{1,7})\b',headline):
        if token in {'THE','ALL','SPOT','WITH','FROM','AND','USDT','USD','USDC','PAIRS','NEW','FOR'}:continue
        if token.upper()==token:symbols.append(token)
    symbols = sorted(set(symbols))[:12]
    # The headline score measures rule-driven *priority*, not truth or return.
    importance = min(100, round(base * .75 + source.reliability * 20 + (8 if symbols else 0), 1))
    return {'event_type':typ, 'importance':importance,
            'classification_confidence': .85 if found else .35,
            'source_reliability':source.reliability, 'symbols':symbols,
            'entities':symbols, 'regions':[]}


def content_fingerprint(headline: str, summary: str) -> str:
    return hashlib.sha256((headline + '\n' + summary).encode()).hexdigest()


def cluster_fingerprint(title: str) -> str:
    words = re.findall(r'[a-z0-9]+', title.lower())
    return hashlib.sha256(' '.join(words[:22]).encode()).hexdigest()


def aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def normalize_symbol(s: str) -> str:
    s = re.sub(r'[^A-Z0-9]', '', str(s).upper())
    for quote in ('USDT', 'USDC', 'BUSD', 'USD', 'BTC', 'ETH'):
        if s.endswith(quote) and len(s) > len(quote) + 1:
            return s[:-len(quote)]
    return s


def link_signals(db: Session, event: IntelligenceEvent) -> int:
    if not event.symbols:
        return 0
    cutoff = event.first_seen_at - timedelta(hours=MAX_LINK_AGE_HOURS)
    # DB work bounded; a shared symbol is CONTEXT, never a proven cause or order.
    possibles = db.scalars(select(SignalRecord).where(SignalRecord.timestamp >= cutoff, SignalRecord.timestamp <= event.first_seen_at).order_by(SignalRecord.timestamp.desc()).limit(300)).all()
    matches = {normalize_symbol(s) for s in event.symbols}
    count = 0
    for sig in possibles:
        sym = normalize_symbol(sig.symbol)
        if sym in matches:
            exists = db.scalar(select(IntelligenceSignalLink.id).where(IntelligenceSignalLink.event_id==event.id, IntelligenceSignalLink.signal_id==sig.id))
            if not exists:
                db.add(IntelligenceSignalLink(event_id=event.id, signal_id=sig.id, symbol=sym, link_reason='symbol_overlap'))
                count += 1
    return count


def store_rows(db: Session, source: Source, rows: list[dict], *, seen_at: datetime | None = None) -> dict[str,int]:
    """Ingest a bounded batch with one existing-rows query and a batched link check.

    Source leasing serializes collectors between Render instances. A competing
    unique insert (e.g. expired lease) fails the source transaction safely;
    the next run retries instead of ever duplicating evidence.
    """
    seen_at=seen_at or now()
    stats={'new':0,'updated':0,'linked':0,'skipped':0}
    cutoff=seen_at-timedelta(days=MAX_EVENT_AGE_DAYS)
    prepared:dict[str,dict]={}
    for row in rows[:MAX_ITEMS]:
        headline=clean(row.get('headline'),750)
        url=normalize_url(row.get('url',''))
        if not headline or not url:
            stats['skipped']+=1;continue
        published=row.get('published_at')
        if isinstance(published,str):published=parsed_date(published)
        if published and (published<cutoff or published>seen_at+timedelta(days=1)):
            stats['skipped']+=1;continue
        external=clean(row.get('external_id') or url,512)
        prepared[external]={'headline':headline,'summary':clean(row.get('summary'),1200),
                            'url':url,'published_at':published}
    if not prepared:return stats
    existing={e.external_id:e for e in db.scalars(select(IntelligenceEvent).where(
        IntelligenceEvent.source_key==source.key,
        IntelligenceEvent.external_id.in_(list(prepared))
    ).with_for_update())}
    changed:list[tuple[IntelligenceEvent,int,str,str,str]]=[]
    for ext,r in prepared.items():
        headline=r['headline'];summary=r['summary']
        checksum=content_fingerprint(headline,summary)
        e=existing.get(ext)
        if e is None:
            e=IntelligenceEvent(source_key=source.key,external_id=ext,category=source.category,
                headline=headline,summary=summary,url=r['url'],content_hash=checksum,
                cluster_key=cluster_fingerprint(headline),published_at=r['published_at'],
                first_seen_at=seen_at,last_seen_at=seen_at,revision=1,
                **classify(headline,summary,source))
            db.add(e)
            changed.append((e,1,headline,summary,checksum))
            stats['new']+=1
        elif e.content_hash!=checksum:
            e.headline=headline;e.summary=summary;e.url=r['url'];e.content_hash=checksum
            e.last_seen_at=seen_at;e.revision+=1;e.cluster_key=cluster_fingerprint(headline)
            for k,v in classify(headline,summary,source).items():setattr(e,k,v)
            changed.append((e,e.revision,headline,summary,checksum))
            stats['updated']+=1
    if changed:
        db.flush() # Bulk IDs returned before writing immutable versions.
        db.add_all(IntelligenceRevision(event_id=e.id,revision=rev,title=t,summary=summary,
                    content_hash=h,observed_at=seen_at) for e,rev,t,summary,h in changed)
        # Link only changed records, in a bounded batch rather than per article.
        active=[e for e,_,_,_,_ in changed if e.symbols]
        if active:
            cutoff=min(e.first_seen_at for e in active)-timedelta(hours=MAX_LINK_AGE_HOURS)
            signals=list(db.scalars(select(SignalRecord).where(SignalRecord.timestamp>=cutoff).order_by(SignalRecord.timestamp.desc()).limit(300)))
            if signals:
                old=set(db.execute(select(IntelligenceSignalLink.event_id,IntelligenceSignalLink.signal_id)
                  .where(IntelligenceSignalLink.event_id.in_([e.id for e in active]))).all())
                for e in active:
                    symbols={normalize_symbol(k) for k in e.symbols}
                    for sig in signals:
                        if (e.id,sig.id) not in old and aware(sig.timestamp)<=aware(e.first_seen_at) and normalize_symbol(sig.symbol) in symbols:
                            db.add(IntelligenceSignalLink(event_id=e.id,signal_id=sig.id,
                                symbol=normalize_symbol(sig.symbol),link_reason='symbol_overlap'))
                            stats['linked']+=1
    return stats


async def fetch_source(client: httpx.AsyncClient, src: Source) -> list[dict]:
    params = None
    if src.kind == 'gdelt':
        params = {'query':GDELT_QUERIES[src.key], 'mode':'artlist','format':'json', 'maxrecords':MAX_ITEMS, 'timespan':'1d', 'sort':'datedesc'}
    elif src.kind == 'github':
        params = {'per_page':12}
    if src.kind == 'sec_submissions':
        email=os.getenv('OSINT_CONTACT_EMAIL', '').strip()
        if not email or '@' not in email:
            raise ValueError('SEC API requires operator contact email')
        response=await client.get(src.endpoint,params=params,headers={'User-Agent':f'Talos Research {email}'})
    else:
        response = await client.get(src.endpoint, params=params)
    response.raise_for_status()
    if len(response.content) > MAX_BODY:
        raise ValueError('Source body exceeds size limit')
    if src.kind == 'rss':
        return extract_xml(response.content)
    if src.kind == 'binance_html':
        return extract_binance_html(response.content)
    if src.kind == 'sec_submissions':
        return extract_sec_submissions(response.json())
    if src.kind == 'gdelt':
        obj = response.json()
        if not isinstance(obj, dict):
            raise ValueError('Unexpected GDELT data')
        return extract_gdelt(obj)
    if src.kind == 'github':
        return extract_github(response.json())
    raise ValueError('Unsupported source kind')


def reserve_source(db: Session, src: Source, at: datetime, force: bool = False) -> bool:
    state = db.get(IntelligenceSourceState, src.key)
    if state is None:
        # Duplicate inserts are benign: roll back nested transaction, then check state.
        try:
            with db.begin_nested():
                db.add(IntelligenceSourceState(source_key=src.key, category=src.category, enabled=True))
                db.flush()
        except IntegrityError:
            pass
    statement = update(IntelligenceSourceState).where(
        IntelligenceSourceState.source_key==src.key,
        IntelligenceSourceState.enabled.is_(True),
        or_(IntelligenceSourceState.lease_until.is_(None), IntelligenceSourceState.lease_until < at),
    )
    if not force:
        statement = statement.where(or_(IntelligenceSourceState.last_attempt_at.is_(None), IntelligenceSourceState.last_attempt_at < at-timedelta(seconds=src.interval)))
    count = db.execute(statement.values(lease_until=at+timedelta(minutes=5), last_attempt_at=at)).rowcount
    db.commit()
    return count == 1


def allowed_to_store() -> bool:
    """Fail closed for nonessential OSINT ingestion before Neon storage quota.

    Only checks PostgreSQL DB size once per six hours, not on every page view.
    On SQLite in local dev there is no Neon storage quota to check.
    """
    global _last_storage_check, _storage_ok
    current=now()
    if _last_storage_check and current-_last_storage_check < timedelta(hours=6):
        return _storage_ok
    try:
        with SessionLocal() as db:
            if db.bind.dialect.name == 'postgresql':
                size=db.scalar(select(func.pg_database_size(func.current_database())))
                _storage_ok = (size or 0) < OSINT_MAX_DB_BYTES
                if not _storage_ok:
                    log.error('OSINT ingestion paused at database size %s; threshold %s',size,OSINT_MAX_DB_BYTES)
            else:
                _storage_ok=True
        _last_storage_check=current
    except Exception:
        _storage_ok=False  # never write more OSINT if quota check cannot be verified
        log.exception('OSINT quota check failed; ingestion paused')
    return _storage_ok


async def collect_all(*, keys: set[str] | None = None, force: bool = False) -> dict[str,dict]:
    result: dict[str,dict] = {}
    sources = [s for s in available_sources() if keys is None or s.key in keys]
    if not sources:
        return result
    if not allowed_to_store():
        return {'guard':{'status':'paused','reason':'neon_storage_safety_or_unavailable'}}
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True,
          headers={'User-Agent':FEED_USER_AGENT, 'Accept':'application/rss+xml,application/xml,application/atom+xml,application/json;q=0.8'}) as client:
        for src in sources:
            with SessionLocal() as db:
                if not reserve_source(db, src, now(), force=force):
                    result[src.key] = {'status':'skipped', 'reason':'not_due_or_disabled'}
                    continue
            try:
                rows = await fetch_source(client, src)
                with SessionLocal() as db:
                    counts = store_rows(db, src, rows)
                    st = db.get(IntelligenceSourceState, src.key)
                    st.last_success_at=now(); st.last_error=None; st.last_count=len(rows); st.lease_until=None
                    db.commit()
                result[src.key] = {'status':'ok','fetched':len(rows),**counts}
            except Exception as exc:
                log.warning('OSINT source %s failed: %s', src.key, exc)
                with SessionLocal() as db:
                    st = db.get(IntelligenceSourceState, src.key)
                    if st:
                        st.last_error=f'{type(exc).__name__}: {str(exc)[:175]}'
                        st.lease_until=None
                        db.commit()
                result[src.key] = {'status':'error','error':type(exc).__name__}
            await asyncio.sleep(.35)
    try:
        with SessionLocal() as db:
            reconcile_signal_links(db)
            db.commit()
    except Exception:
        log.exception("OSINT signal reconciliation failed")
    return result


async def collector_loop() -> None:
    await asyncio.sleep(60)
    while True:
        if os.getenv('OSINT_ENABLED', 'true').lower() in {'1','true','yes','on'}:
            try:
                await collect_all()
            except Exception:
                log.exception('OSINT collection cycle failed')
        await asyncio.sleep(INTERVAL_SECONDS)


def prune_old(db: Session, days: int = RETENTION_DAYS) -> int:
    """Optional manual maintenance; NEVER remove provenance of current events while referenced."""
    cutoff=now()-timedelta(days=days)
    event_ids = select(IntelligenceEvent.id).where(IntelligenceEvent.first_seen_at < cutoff).limit(300)
    ids = list(db.scalars(event_ids))
    if not ids:
        return 0
    # Preserve linked evidence. No automatic deletion of audit trails.
    linked = set(db.scalars(select(IntelligenceSignalLink.event_id).where(IntelligenceSignalLink.event_id.in_(ids))))
    ids=[i for i in ids if i not in linked]
    if ids:
        db.execute(delete(IntelligenceRevision).where(IntelligenceRevision.event_id.in_(ids)))
        db.execute(delete(IntelligenceEvent).where(IntelligenceEvent.id.in_(ids)))
    return len(ids)


def reconcile_signal_links(db: Session, *, horizon_hours: int = 72) -> int:
    """Attach independently arriving signals/news without relabeling them as causal."""
    cutoff=now()-timedelta(hours=horizon_hours)
    events=list(db.scalars(select(IntelligenceEvent).where(IntelligenceEvent.first_seen_at>=cutoff,IntelligenceEvent.importance>=55).order_by(IntelligenceEvent.id.desc()).limit(200)))
    signals=list(db.scalars(select(SignalRecord).where(SignalRecord.timestamp>=cutoff).order_by(SignalRecord.timestamp.desc()).limit(250)))
    if not events or not signals:
        return 0
    event_ids=[e.id for e in events]
    existing=set(db.execute(select(IntelligenceSignalLink.event_id,IntelligenceSignalLink.signal_id).where(IntelligenceSignalLink.event_id.in_(event_ids))).all())
    new=0
    for event in events:
        wanted={normalize_symbol(x) for x in event.symbols}
        for sig in signals:
            # The linked signal must exist within the 72-hour context window.
            if (event.id,sig.id) not in existing and normalize_symbol(sig.symbol) in wanted and wanted:
                db.add(IntelligenceSignalLink(event_id=event.id,signal_id=sig.id,symbol=normalize_symbol(sig.symbol),link_reason='symbol_overlap'))
                new+=1
    return new


def point_in_time_features(db:Session, asset:str, observed_by:datetime, hours:int=24) -> dict:
    """Use ONLY revisions available as of t. Avoid hindsight from corrected headlines.

    This is a research adapter, not a buy/sell predictor. Call with bar-replay clock.
    """
    key=normalize_symbol(asset)
    events=list(db.scalars(select(IntelligenceEvent).where(
        IntelligenceEvent.first_seen_at <= observed_by,
        IntelligenceEvent.first_seen_at >= observed_by-timedelta(hours=hours),
    ).order_by(IntelligenceEvent.id.desc()).limit(500)))
    sources={s.key:s for s in available_sources()}
    eligible=[]
    latest={}
    if events:
        rows=db.scalars(select(IntelligenceRevision).where(
            IntelligenceRevision.event_id.in_([e.id for e in events]),
            IntelligenceRevision.observed_at<=observed_by,
        ).order_by(IntelligenceRevision.event_id,IntelligenceRevision.revision.desc())).all()
        for rev in rows:
            latest.setdefault(rev.event_id,rev)
    for e in events:
        r=latest.get(e.id)
        if r is None: continue
        source=sources.get(e.source_key) or Source(e.source_key,e.source_key,e.category,'unknown',e.url,e.source_reliability)
        view=classify(r.title,r.summary,source)
        if key in {normalize_symbol(s) for s in view['symbols']}:
            eligible.append((e,view,r))
    scores=[v['importance'] for _,v,_ in eligible]
    return {'symbol':key,'as_of':observed_by,'window_hours':hours,'available':bool(eligible),
            'features':{'osint_event_count':len(eligible),'osint_source_count':len({e.source_key for e,_,_ in eligible}),
                        'osint_max_priority':max(scores) if scores else None,
                        'osint_mean_priority':round(sum(scores)/len(scores),2) if scores else None,
                        'osint_high_priority_count':sum(x>=88 for x in scores)},
            'evidence':[{'event_id':e.id,'revision':r.revision,'observed_at':r.observed_at,'type':v['event_type'],
                         'importance':v['importance']} for e,v,r in eligible[:50]]}
