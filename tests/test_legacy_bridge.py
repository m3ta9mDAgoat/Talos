"""Integration checks: authenticated incremental Telegram bridge; no Bot API calls."""
from __future__ import annotations
import json,sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from core.telegram import replay
from server.api import create_app
from server.storage import Store

@pytest.fixture
def setup_bridge(monkeypatch,tmp_path):
    monkeypatch.setenv('TALOS_LAB_ADMIN_TOKEN','A'*40)
    monkeypatch.setenv('TALOS_LAB_WORKER_TOKEN','W'*40)
    store=Store('sqlite:///'+str(tmp_path/'bridge.db'))
    cli=TestClient(create_app(store))
    return cli,store,{'Authorization':'Bearer '+'A'*40},{'Authorization':'Bearer '+'W'*40}


def page():
    x={'chat_id':'legacy:-1001','message_id':'246','timestamp':'2026-01-01T12:00:00+00:00','text':'BUY AAPL'}
    return [dict(x,legacy_id=100,event_type='new',revision='100'),
            dict(x,legacy_id=105,event_type='edit',revision='105',timestamp='2026-01-01T12:10:00+00:00',text='SELL AAPL'),
            dict(x,legacy_id=111,event_type='delete',revision='111',timestamp='2026-01-01T12:25:00+00:00',text='')]


def test_bridge_auth_idempotence_revisions_and_restart(setup_bridge,tmp_path):
    c,store,a,w=setup_bridge
    assert c.get('/api/v2/research/telegram/legacy/status').status_code==401
    assert c.post('/api/v2/research/telegram/legacy/batch',json={'events':page()}).status_code==401
    assert c.post('/api/v2/research/telegram/legacy/batch',headers=w,json={'events':page()}).status_code==401
    events=page()
    first=c.post('/api/v2/research/telegram/legacy/batch',headers=a,json={'events':events})
    assert first.status_code==201,first.text
    report=first.json()
    assert report['inserted']==3 and report['stored_total']==3 and report['last_legacy_id']==111
    assert report['notifications_sent'] is False and report['source_read_only'] is True
    archive=store.get_archive(report['archive_id'])
    assert len(archive)==3
    assert replay(archive,cutoff='2026-01-01T12:15:00+00:00')['edits']==1
    assert replay(archive)['active_messages']==0
    again=c.post('/api/v2/research/telegram/legacy/batch',headers=a,json={'events':events})
    assert again.status_code==201 and again.json()['inserted']==0
    assert again.json()['archive_id']==report['archive_id']
    changed=[dict(e) for e in events];changed[1]['text']='BUY TSLA'
    refuse=c.post('/api/v2/research/telegram/legacy/batch',headers=a,json={'events':changed})
    assert refuse.status_code==422 and 'immutable' in refuse.text
    assert c.get('/api/v2/research/telegram/legacy/status',headers=a).json()['cursor']==111
    persisted=Store('sqlite:///'+str(tmp_path/'bridge.db'))
    assert persisted.legacy_stats()['count']==3


def test_bridge_rejects_bad_time_and_duplicate_ids(setup_bridge):
    cli,store,a,w=setup_bridge
    x=page()
    assert cli.post('/api/v2/research/telegram/legacy/batch',headers=a,json={'events':[x[0],x[0]]}).status_code==422
    broken=[dict(x[0],timestamp='2026-01-01')]
    assert cli.post('/api/v2/research/telegram/legacy/batch',headers=a,json={'events':broken}).status_code==422
    assert cli.post('/api/v2/research/telegram/legacy/batch',headers=a,json={'events':[dict(x[0],legacy_id=-1)]}).status_code==422
    assert store.legacy_stats()['count']==0


def test_bridge_reader_simulated_two_services(setup_bridge):
    from scripts.legacy_bridge_sync import sync_once
    import httpx
    lab,store,a,w=setup_bridge
    source_events=page()
    legacy_get_count=[]
    class DummyTransport:
        def __call__(self,request:httpx.Request):
            path=request.url.path
            if path=='/api/v1/settings/research-bridge/telegram/events':
                assert request.headers['x-talos-bridge-key']=='Q'*40
                cursor=int(request.url.params['after_id'])
                rows=[e for e in source_events if e['legacy_id']>cursor]
                legacy_get_count.append(cursor)
                batch=rows[:2]
                return httpx.Response(200,json={'schema':'talos.legacy_telegram.page.v1','read_only':True,
                    'events':batch,'next_after_id':batch[-1]['legacy_id'] if batch else cursor,
                    'has_more':len(rows)>2})
            r=lab.request(request.method,request.url.path,headers=dict(request.headers),content=request.content)
            return httpx.Response(r.status_code,content=r.content,headers=dict(r.headers))
    with httpx.Client(transport=httpx.MockTransport(DummyTransport()),base_url='https://lab.example.test') as client:
        first=sync_once(client,'https://legacy.example.test','https://lab.example.test','Q'*40,'A'*40)
        assert first['new_observations']==3 and first['pages']==2
        assert first['last_legacy_id']==111
        second=sync_once(client,'https://legacy.example.test','https://lab.example.test','Q'*40,'A'*40)
        assert second['new_observations']==0
        assert legacy_get_count==[0,105,111]
    assert store.legacy_stats()['count']==3


def test_brand_asset_original_unmodified():
    # The approved v4.7.8 splash art is identical in old/updated frontend and Lab.
    import hashlib
    root=Path(__file__).resolve().parents[2]
    src=root/'Frontend/frontend/public/talos-splash.png'
    lab=root/'ResearchPlane/web/talos-splash.png'
    if not (src.exists() and lab.exists()):
        # GitHub-ready standalone package: compare Lab against original release art SHA256.
        lab=Path(__file__).resolve().parents[1]/'web/talos-splash.png'
        assert hashlib.sha256(lab.read_bytes()).hexdigest()=='5acc1e51557d4a4c41c4d663eb153959bcff2dfd3e783f3846feb97c288895b9'
    else:
        assert hashlib.sha256(src.read_bytes()).digest()==hashlib.sha256(lab.read_bytes()).digest()
