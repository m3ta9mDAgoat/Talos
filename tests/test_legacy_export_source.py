"""Execute the *actual* legacy export handler with isolated SQL records.
Does not import the giant legacy application or start its Telegram watcher.
"""
from __future__ import annotations
import ast,sys,types
import pytest
from datetime import datetime,timezone
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import Integer,String,Text,DateTime,create_engine
from sqlalchemy.orm import DeclarativeBase,Mapped,mapped_column,sessionmaker

class Base(DeclarativeBase):pass
class RawEventRecord(Base):
    __tablename__='raw_events'
    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    source_type:Mapped[str]=mapped_column(String(80))
    source_identifier:Mapped[str]=mapped_column(String(500))
    external_id:Mapped[str]=mapped_column(String(255))
    occurred_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    ingested_at:Mapped[datetime]=mapped_column(DateTime(timezone=True))
    event_type:Mapped[str]=mapped_column(String(20))
    text:Mapped[str]=mapped_column(Text)


def test_readonly_paginated_exact_legacy_handler(monkeypatch,tmp_path):
    path=Path(__file__).resolve().parents[2]/'Backend/backend/app/main.py'
    if not path.exists():pytest.skip('Requires separately provided v4.7.8 Backend source for cross-repo verification')
    source=path.read_text()
    tree=ast.parse(source)
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='export_legacy_telegram_events')
    fn.decorator_list=[]
    code=compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec')
    namespace={}
    exec(code,namespace)
    eng=create_engine('sqlite:///'+str(tmp_path/'original_legacy.db'))
    Base.metadata.create_all(eng)
    SessionLocal=sessionmaker(bind=eng)
    with SessionLocal() as s:
        for i,typ,kind,when in [
            (1,'telegram','message','2025-01-01T13:00:00+00:00'),
            (2,'telegram','edit','2025-01-01T14:00:00+00:00'),
            (3,'news','message','2025-01-01T15:00:00+00:00'),
            (4,'telegram','delete','2025-01-01T16:00:00+00:00')]:
            # Original event was published earlier; export MUST use ingested_at.
            s.add(RawEventRecord(id=i,source_type=typ,source_identifier='my-channel',external_id='444',
                occurred_at=datetime.fromisoformat('2020-01-01T00:00:00+00:00'),
                ingested_at=datetime.fromisoformat(when),event_type=kind,text='BUY AAPL'))
        s.commit()
    modules={
        'app':types.ModuleType('app'), 'app.db':types.ModuleType('app.db'),
        'app.db.session':types.ModuleType('app.db.session'),
        'app.models':types.ModuleType('app.models'),
        'app.models.raw_event':types.ModuleType('app.models.raw_event')}
    modules['app.db.session'].SessionLocal=SessionLocal
    modules['app.models.raw_event'].RawEventRecord=RawEventRecord
    for name,mod in modules.items():monkeypatch.setitem(sys.modules,name,mod)
    export=namespace['export_legacy_telegram_events']
    p=export(after_id=0,limit=2)
    assert p['read_only'] is True and p['notifications_sent'] is False
    assert p['has_more'] is True and p['next_after_id']==2
    assert [e['event_type'] for e in p['events']]==['new','edit']
    assert all(e['timestamp'].startswith('2025-') for e in p['events'])
    assert all(e['original_occurred_at'].startswith('2020-') for e in p['events'])
    next_page=export(after_id=p['next_after_id'],limit=2)
    assert next_page['has_more'] is False and next_page['next_after_id']==4
    assert len(next_page['events'])==1 and next_page['events'][0]['event_type']=='delete'
    assert next_page['events'][0]['text']==''
    with SessionLocal() as s:
        assert s.query(RawEventRecord).count()==4
    from pytest import raises
    with raises(HTTPException):export(after_id=-1,limit=2)
    with raises(HTTPException):export(after_id=0,limit=200)


def test_bridge_key_is_scoped_to_readonly_export(monkeypatch):
    """Exercise the actual v4.7.8 admin middleware, isolated from workers."""
    import asyncio,os,types,ast
    path=Path(__file__).resolve().parents[2]/'Backend/backend/app/main.py'
    if not path.exists():pytest.skip('Requires separately provided v4.7.8 Backend source for cross-repo verification')
    tree=ast.parse(path.read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='protect_admin_api')
    fn.decorator_list=[]
    globals_={'os':os,'_ADMIN_PREFIXES':('/api/v1/settings','/api/v1/live'),
              'admin_exists':lambda:False,'validate_session':lambda token:False,
              'SESSION_COOKIE':'talos_auth_cookie',
              'JSONResponse':__import__('fastapi').responses.JSONResponse}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),globals_)
    protect=globals_['protect_admin_api']
    key='a'*40
    monkeypatch.setenv('TALOS_RESEARCH_BRIDGE_SHARED_SECRET',key)
    class Req:
        def __init__(self,path,presented):
            self.url=types.SimpleNamespace(path=path)
            self.headers={'x-talos-bridge-key':presented}
            self.cookies={}
    async def next_(r):return types.SimpleNamespace(status_code=200)
    bridge='/api/v1/settings/research-bridge/telegram/events'
    assert asyncio.run(protect(Req(bridge,key),next_)).status_code==200
    assert asyncio.run(protect(Req(bridge,'bad'),next_)).status_code==428
    assert asyncio.run(protect(Req('/api/v1/live/order',key),next_)).status_code==428
    assert asyncio.run(protect(Req('/api/v1/settings/bot-token',key),next_)).status_code==428
    monkeypatch.delenv('TALOS_RESEARCH_BRIDGE_SHARED_SECRET')
    assert asyncio.run(protect(Req(bridge,key),next_)).status_code==428
