from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
import httpx

from app.core.enums import MarketType, Side, SignalStatus, StopTrigger
from app.schemas.signal import EntrySpec, NormalizedSignal, StopSpec
from app.services.security import get_secret


def configured() -> bool:
    s=get_secret('ai')
    return bool(s.get('base_url') and s.get('model'))


def parse_signal(text:str, *, timestamp:datetime, source_id:str, source_message_id:str, default_quote:str='USDT') -> NormalizedSignal | None:
    cfg=get_secret('ai');base=(cfg.get('base_url') or '').rstrip('/');model=cfg.get('model');key=cfg.get('api_key')
    if not base or not model:return None
    prompt=("Extract a crypto spot trading signal from the message. Return JSON only with keys: "
            "is_signal(boolean), symbol(string like BTC/USDT), side(buy/sell), entry_type(market/price/range), entry_price, entry_min, entry_max, "
            "targets(array numbers), stop_price(number|null), stop_trigger(price_touch/candle_close), stop_timeframe(string|null), confidence(0..1). "
            "Do not infer missing numeric values. If this is commentary/result/admin and not a new signal set is_signal=false. Default quote is "+default_quote+". Message:\n"+text[:6000])
    headers={'Content-Type':'application/json'}
    if key:headers['Authorization']=f'Bearer {key}'
    try:
        with httpx.Client(timeout=45) as client:
            r=client.post(base+'/chat/completions',headers=headers,json={'model':model,'messages':[{'role':'system','content':'Return strict JSON only.'},{'role':'user','content':prompt}],'temperature':0.0});r.raise_for_status();raw=r.json()['choices'][0]['message']['content']
        raw=raw.strip().removeprefix('```json').removesuffix('```').strip();data=json.loads(raw)
        if not data.get('is_signal'):return None
        symbol=str(data.get('symbol') or '').upper().replace('|','/').replace('-','/')
        if '/' not in symbol and symbol:symbol=f'{symbol}/{default_quote}'
        if not symbol:return None
        et=data.get('entry_type') or 'market';entry=None
        if et=='range' and data.get('entry_min') is not None and data.get('entry_max') is not None:entry=EntrySpec(type='range',min=float(data['entry_min']),max=float(data['entry_max']))
        elif et=='price' and data.get('entry_price') is not None:entry=EntrySpec(type='price',price=float(data['entry_price']))
        elif et=='market':entry=EntrySpec(type='market')
        stop=None
        if data.get('stop_price') is not None:
            trig=StopTrigger.CANDLE_CLOSE if data.get('stop_trigger')=='candle_close' else StopTrigger.PRICE_TOUCH
            stop=StopSpec(price=float(data['stop_price']),trigger=trig,timeframe=(data.get('stop_timeframe') if trig==StopTrigger.CANDLE_CLOSE else None))
        return NormalizedSignal(strategy_id='telegram-copy',strategy_version='ai-fallback-1.0',source='telegram',source_id=source_id,source_message_id=source_message_id,timestamp=timestamp,market_type=MarketType.SPOT,symbol=symbol,side=Side(str(data.get('side') or 'buy').lower()),entry=entry,stop=stop,targets=[float(x) for x in data.get('targets') or []],confidence=max(0.0,min(1.0,float(data.get('confidence') or .65))),status=SignalStatus.PARSED,raw_text=text,metadata={'parser':'ai_fallback'})
    except Exception:
        return None
