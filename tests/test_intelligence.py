"""Offline tests: production ingestion is never simulated as market data."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from app.db.base import Base
from app.models.intelligence import IntelligenceEvent, IntelligenceRevision, IntelligenceSignalLink
from app.models.signal import SignalRecord
from app.services.intelligence import (
    Source, classify, extract_xml, extract_gdelt, extract_github, store_rows,
    normalize_symbol, normalize_url, parsed_date,
)

SRC=Source('test_source','test','crypto','rss','https://example.org/feed',.94)
T=datetime(2026,10,9,13,1,tzinfo=timezone.utc)


def test_rss_atom_and_xxe_rejection():
    rss=b'''<rss version="2.0"><channel><item><guid>123</guid><title>Bitcoin is listed</title><link>https://example.org/a?utm_source=spam</link><pubDate>Fri, 09 Oct 2026 12:01:00 GMT</pubDate><description>BTC story</description></item></channel></rss>'''
    rows=extract_xml(rss)
    assert len(rows)==1
    assert rows[0]['headline']=='Bitcoin is listed'
    assert rows[0]['url']=='https://example.org/a'
    assert rows[0]['external_id']=='123'
    atom=b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>a123</id><title>ECB interest rate decision</title><link rel="alternate" href="https://example.org/ebc"/><updated>2026-10-09T10:00:00Z</updated></entry></feed>'''
    assert extract_xml(atom)[0]['published_at'].tzinfo is not None
    try:
        extract_xml(b'<!DOCTYPE foo [ <!ENTITY a "bad"> ]><rss/>')
    except ValueError:
        pass
    else:
        assert False, 'DOCTYPE must be refused'


def test_provider_schemas_and_timestamps():
    data={'articles':[{'url':'https://publisher.net/a','title':'Oil pipeline closure','seendate':'20261009T130001Z'}]}
    assert extract_gdelt(data)[0]['published_at'].year==2026
    gh=[{'id':20,'name':'v2.0.1','html_url':'https://github.com/o/r/releases/tag/v2.0.1','published_at':'2026-10-09T13:00:00Z','body':'Fixed'}]
    assert extract_github(gh)[0]['external_id']=='20'


def test_classifier_priority_and_no_ticker_hallucinations():
    a=classify('Bitcoin security exploit triggers loss','',SRC)
    assert a['event_type']=='exploit' and a['symbols']==['BTC']
    b=classify('Exchange will delist $ABC next week','',SRC)
    assert b['event_type']=='delisting' and 'ABC' in b['symbols']
    c=classify('BREAKING: FREE DOG SALE', '', SRC)
    assert c['symbols']==[]  # All caps by itself is NOT a symbol
    assert normalize_symbol('ETH/USDT')=='ETH'


def test_audit_revision_idempotency_timestamp_links():
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        sig=SignalRecord(strategy_id='test',strategy_version='1',source='telegram',timestamp=T-timedelta(hours=1),symbol='BTC/USDT',side='buy',market_type='spot',status='new')
        db.add(sig);db.commit()
        data=[{'external_id':'001','url':'https://example.org/001','headline':'Bitcoin exploit announced','summary':'Initial','published_at':T-timedelta(minutes=10)}]
        a=store_rows(db,SRC,data,seen_at=T)
        db.commit()
        assert a['new']==1 and a['linked']==1
        event=db.scalar(select(IntelligenceEvent))
        assert event.first_seen_at.replace(tzinfo=timezone.utc)==T
        assert db.scalar(select(func.count()).select_from(IntelligenceRevision))==1
        b=store_rows(db,SRC,data,seen_at=T+timedelta(minutes=2)); db.commit()
        assert b['new']==0 and b['updated']==0
        assert db.scalar(select(func.count()).select_from(IntelligenceRevision))==1
        data[0]['summary']='Source corrected: the exploit was contained'
        c=store_rows(db,SRC,data,seen_at=T+timedelta(minutes=3)); db.commit()
        assert c['updated']==1
        event=db.scalar(select(IntelligenceEvent))
        assert event.first_seen_at.replace(tzinfo=timezone.utc)==T
        assert event.revision==2
        assert db.scalar(select(func.count()).select_from(IntelligenceRevision))==2
        assert db.scalar(select(func.count()).select_from(IntelligenceSignalLink))==1


def test_stale_and_invalid_events_rejected():
    engine=create_engine('sqlite://'); Base.metadata.create_all(engine)
    with Session(engine) as db:
        older=T-timedelta(days=70)
        r=store_rows(db,SRC,[{'url':'https://example.org/older','headline':'Old report','published_at':older},{'url':'javascript:alert(1)','headline':'Unsafe'}],seen_at=T)
        assert r['new']==0 and r['skipped']==2
        assert normalize_url('javascript:alert(1)')==''


def test_binance_and_sec_optional_adapters():
    from app.services.intelligence import extract_binance_html, extract_sec_submissions
    markup=b'<a href="/en/support/announcement/detail/ab42">Binance to delist Example Token ABC on Friday</a><a href="/blog/x">Irrelevant</a>'
    r=extract_binance_html(markup)
    assert len(r)==1 and r[0]['headline'].startswith('Binance to delist')
    data={'name':'Acme Inc','cik':123456,'filings':{'recent':{'form':['8-K'],'accessionNumber':['000123456-26-000010'],'filingDate':['2026-10-09'],'primaryDocument':['acme-8k.htm']}}}
    rows=extract_sec_submissions(data)
    assert rows[0]['headline']=='Acme Inc filed 8-K with the SEC'
    assert rows[0]['url'].startswith('https://www.sec.gov/Archives/')


def test_point_in_time_does_not_leak_later_revision():
    from app.services.intelligence import point_in_time_features
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with Session(engine) as db:
        initial=[{'external_id':'evt','url':'https://example.org/news/1','headline':'Bitcoin network update', 'summary':'Report','published_at':T}]
        store_rows(db,SRC,initial,seen_at=T)
        db.commit()
        later=dict(initial[0]);later['headline']='Bitcoin exploit reported'
        store_rows(db,SRC,[later],seen_at=T+timedelta(hours=2));db.commit()
        old=point_in_time_features(db,'BTC',T+timedelta(minutes=30))
        recent=point_in_time_features(db,'BTC',T+timedelta(hours=3))
        assert old['available'] and recent['available']
        assert old['evidence'][0]['type']=='report'
        assert recent['evidence'][0]['type']=='exploit'
