from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from app.db.session import SessionLocal
from app.models.source import SourceRecord
from app.services.ingestion import persist_events
from app.plugins.contracts import Event
from app.services.security import get_secret


def utcnow():
    return datetime.now(timezone.utc)


def _get_path(value: Any, path: str | None) -> Any:
    if not path:
        return value
    cur = value
    for part in path.split('.'):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)]
        else:
            return None
    return cur


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value:
        try:
            return datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(timezone.utc)
        except Exception:
            try:
                dt=parsedate_to_datetime(str(value)); return dt.astimezone(timezone.utc)
            except Exception:
                pass
    return utcnow()


def _event(source: SourceRecord, external_id: str, text: str | None, payload: dict[str, Any], occurred_at: datetime | None = None, event_type: str = "item") -> Event:
    return Event(
        source=str(source.type.value if hasattr(source.type, "value") else source.type),
        timestamp=occurred_at or utcnow(),
        external_id=external_id,
        payload={**payload, "text": text},
        metadata={"source_id": source.id, "adapter": (source.config or {}).get("adapter", str(source.type)), "event_type": event_type},
    )


async def _rss(source: SourceRecord) -> list[Event]:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent":"Talos/3.0 research client"}) as client:
        res=await client.get(source.identifier); res.raise_for_status()
    root=ET.fromstring(res.content); out=[]
    nodes=root.findall('.//item')
    if not nodes:
        ns={'a':'http://www.w3.org/2005/Atom'}; nodes=root.findall('.//a:entry',ns)
    for idx,item in enumerate(nodes[:500]):
        def txt(tag):
            el=item.find(tag) or item.find('{http://www.w3.org/2005/Atom}'+tag)
            return (el.text or '').strip() if el is not None and el.text else ''
        title=txt('title'); summary=txt('description') or txt('summary') or txt('content'); link=txt('link')
        if not link:
            a=item.find('{http://www.w3.org/2005/Atom}link'); link=(a.get('href') if a is not None else '')
        external=txt('guid') or txt('id') or link or hashlib.sha1((title+summary+str(idx)).encode()).hexdigest()
        when=_dt(txt('pubDate') or txt('published') or txt('updated'))
        out.append(_event(source,str(external),'\n'.join(x for x in [title,summary] if x),{'title':title,'url':link},when,'rss_item'))
    return out


async def _http_json(source: SourceRecord) -> list[dict[str, Any]]:
    cfg=source.config or {}; secret=get_secret(f"source:{source.id}")
    headers=dict(cfg.get('headers') or {}); headers.update(secret.get('headers') or {})
    method=str(cfg.get('method','GET')).upper()
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        res=await client.request(method, source.identifier, headers=headers, params=cfg.get('params') or {})
        res.raise_for_status(); data=res.json()
    items=_get_path(data,cfg.get('items_path'))
    if not isinstance(items,list): items=[items]
    out=[]
    for item in items:
        if not isinstance(item,dict): item={'value':item}
        external=str(_get_path(item,cfg.get('id_field')) or hashlib.sha1(json.dumps(item,sort_keys=True,default=str).encode()).hexdigest())
        text=str(_get_path(item,cfg.get('text_field')) or item.get('text') or item.get('title') or '')
        when=_dt(_get_path(item,cfg.get('time_field')))
        out.append(_event(source,external,text,item,when,'api_item'))
    return out


async def _web(source: SourceRecord) -> list[dict[str, Any]]:
    cfg=source.config or {}; secret=get_secret(f"source:{source.id}")
    headers={'User-Agent':'Talos/3.0 research client'}; headers.update(secret.get('headers') or {})
    async with httpx.AsyncClient(timeout=30,follow_redirects=True,headers=headers) as client:
        res=await client.get(source.identifier); res.raise_for_status()
    soup=BeautifulSoup(res.text,'html.parser')
    item_selector=cfg.get('item_selector') or 'article'
    text_selector=cfg.get('text_selector')
    link_selector=cfg.get('link_selector')
    out=[]
    for idx,node in enumerate(soup.select(item_selector)[:500]):
        tnode=node.select_one(text_selector) if text_selector else node
        text=' '.join(tnode.get_text(' ',strip=True).split()) if tnode else ''
        lnode=node.select_one(link_selector) if link_selector else node.find('a')
        link=lnode.get('href') if lnode and hasattr(lnode,'get') else None
        external=str(link or hashlib.sha1(text.encode()).hexdigest() or idx)
        out.append(_event(source,external,text,{'url':link,'html_tag':node.name},utcnow(),'web_item'))
    return out


async def sync_source(source_id:int) -> dict[str,Any]:
    with SessionLocal() as db:
        source=db.get(SourceRecord,source_id)
        if not source: raise ValueError('Source not found')
        cfg=dict(source.config or {}); adapter=cfg.get('adapter')
        if adapter in (None,'telegram') and str(getattr(source.type,'value',source.type))=='telegram':
            from app.plugins.datasources.telegram import TelegramSource
            limit=int(cfg.get('sync_limit',500)); events=await TelegramSource().latest(channel=source.identifier,limit=limit)
        elif adapter=='rss' or str(getattr(source.type,'value',source.type))=='rss':
            events=await _rss(source)
        elif adapter in {'http_json','x_api'}:
            events=await _http_json(source)
        elif adapter=='web':
            events=await _web(source)
        elif adapter=='webhook':
            return {'source_id':source_id,'adapter':'webhook','fetched':0,'inserted':0,'skipped':0,'note':'Webhook waits for POST events.'}
        else:
            raise ValueError(f'Unsupported source adapter: {adapter}')
        inserted, skipped=persist_events(db,source.identifier,events)
        source.config={**cfg,'last_sync_at':utcnow().isoformat(),'last_sync_fetched':len(events),'last_error':None}
        db.commit()
        return {'source_id':source_id,'adapter':adapter,'fetched':len(events),'inserted':inserted,'skipped':skipped}


async def test_source(source_id:int) -> dict[str,Any]:
    with SessionLocal() as db:
        source=db.get(SourceRecord,source_id)
        if not source: raise ValueError('Source not found')
        adapter=(source.config or {}).get('adapter')
        if adapter in (None,'telegram') and str(getattr(source.type,'value',source.type))=='telegram':
            from app.plugins.datasources.telegram import TelegramSource
            info=await TelegramSource().access_info(channel=source.identifier)
            return {'ok':True,'adapter':'telegram',**info}
    result=await sync_source(source_id)
    return {'ok':True,**result}
