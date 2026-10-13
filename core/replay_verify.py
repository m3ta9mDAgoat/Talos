"""Point-in-time recommendation/claim verification for imported source observations.

All sources (Telegram, X, RSS, web, user-supplied archives) use identical event-time
rules. This checks narrowly defined BUY/SELL recommendations against uploaded OHLCV;
it does NOT authenticate authorship, validate all natural-language claims or backtest
broker-executable orders. Editing/deletion never rewrites earlier recommendations.
"""
from __future__ import annotations
from bisect import bisect_right
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json, re
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal

SOURCE = Literal['telegram','x','rss','website','other']
EVENT = Literal['new','edit','delete']
PATTERN = re.compile(r'\b(BUY|SELL)\s+([A-Z0-9][A-Z0-9_./:=^-]{0,30})\b', re.I)

class VerificationEvent(BaseModel):
    post_id: str = Field(min_length=1,max_length=180)
    author: str = Field(min_length=1,max_length=180)
    first_observed_at: datetime
    claimed_published_at: datetime|None = None
    event_type: EVENT = 'new'
    text: str = Field(default='',max_length=12000)
    @field_validator('first_observed_at','claimed_published_at')
    @classmethod
    def aware(cls, dt):
        if dt is not None and dt.utcoffset() is None:raise ValueError('Timezone offset required')
        return dt.astimezone(timezone.utc) if dt else None

class VerificationRequest(BaseModel):
    source_kind: SOURCE
    source_label: str = Field(min_length=1,max_length=180)
    symbol: str = Field(min_length=1,max_length=32,pattern=r'^[A-Z0-9][A-Z0-9_./:=^-]{0,30}$')
    dataset_id: str = Field(min_length=64,max_length=64,pattern=r'^[a-f0-9]{64}$')
    horizon_sessions: int = Field(default=5,ge=1,le=120)
    fee_bps: float = Field(default=10,ge=0,le=500)
    slippage_bps: float = Field(default=2,ge=0,le=300)
    events: list[VerificationEvent] = Field(min_length=1,max_length=500)


def verify(body: VerificationRequest, dataset: dict) -> dict:
    from .market_data import normalize
    if dataset['symbol'].upper()!=body.symbol.upper():raise ValueError('Dataset symbol differs from requested symbol')
    bars=normalize(dataset['bars'])
    dates=[b['date'] for b in bars]
    # Always use observed time, never an earlier claimed publication time.
    # Event revisions have their own observed timestamp. They are counted in the
    # audit but are NOT new standalone trading recommendations by default.
    posts=set();seen_events=set();trades=[];revisions=[];excluded=[]
    canonical=[]
    for ev in sorted(body.events,key=lambda e:(e.first_observed_at,e.post_id,e.event_type)):
        serial=(ev.post_id,ev.event_type,ev.first_observed_at.isoformat(),ev.text)
        if serial in seen_events:continue
        seen_events.add(serial)
        event_day=ev.first_observed_at.date().isoformat()
        base={'post_id':ev.post_id,'author':ev.author,'first_observed_at':ev.first_observed_at.isoformat(),
              'event_type':ev.event_type,'claimed_published_at':ev.claimed_published_at.isoformat() if ev.claimed_published_at else None}
        canonical.append({**base,'text_sha256':sha256(ev.text.encode()).hexdigest()})
        if ev.event_type!='new':
            revisions.append({**base,'status':'revision_preserved_not_retroactively_executed'})
            continue
        if ev.post_id in posts:
            excluded.append({**base,'reason':'duplicate_original_post_id'})
            continue
        posts.add(ev.post_id)
        m=PATTERN.search(ev.text)
        if not m or m.group(2).upper()!=body.symbol.upper():
            excluded.append({**base,'reason':'not_explicit_matching_buy_sell_recommendation'})
            continue
        # Require the next supplied daily session strictly AFTER UTC observed date.
        entry_ix=bisect_right(dates,event_day)
        last_ix=entry_ix+body.horizon_sessions-1
        if last_ix>=len(bars):
            excluded.append({**base,'reason':'insufficient_future_market_data'})
            continue
        entry=Decimal(str(bars[entry_ix]['open']))
        exit_=Decimal(str(bars[last_ix]['close']))
        side=m.group(1).upper()
        slippage=Decimal(str(body.slippage_bps))/Decimal('10000')
        spread=Decimal(str(body.fee_bps))/Decimal('10000')
        # Price-based recommendation score, not a short-sale order simulator.
        if side=='BUY':
            start=entry*(1+slippage);end=exit_*(1-slippage)
            gross=(end/start)-1
        else:
            start=entry*(1-slippage);end=exit_*(1+slippage)
            gross=(start-end)/start
        net=gross-2*spread
        classification='WIN' if net>0 else 'LOSS' if net<0 else 'FLAT'
        trades.append({**base,'side':side,'symbol':body.symbol,'entry_date':dates[entry_ix],
                       'exit_date':dates[last_ix],'entry_open':float(entry),'exit_close':float(exit_),
                       'net_return_pct':round(float(net*100),5),'outcome':classification,
                       'policy':'next_supplied_session_open; exit_after_N_sessions_close; price-only; not broker fills'})
    wins=sum(v['outcome']=='WIN' for v in trades)
    losses=sum(v['outcome']=='LOSS' for v in trades)
    summary={'source_kind':body.source_kind,'source_label':body.source_label,
             'recommendations_scored':len(trades),'wins':wins,'losses':losses,
             'flat':len(trades)-wins-losses,'revisions_retained':len(revisions),
             'excluded':len(excluded),'accuracy_pct_of_scored':round(wins/len(trades)*100,4) if trades else None}
    return {'schema':'talos.replay.cross_source_validation.v1','status':'COMPLETED',
            'source_authenticated':False,'archived_post_authenticity_verified':False,
            'data_authenticated':False,'no_lookahead_cutoff':'first_observed_at_utc_day_next_bar_open',
            'notification_count':0,'live_trading_enabled':False,'synthetic_execution_assumptions':True,
            'result_sha256':sha256(json.dumps(canonical,sort_keys=True).encode()).hexdigest(),
            'dataset_id':body.dataset_id,'symbol':body.symbol,'horizon_sessions':body.horizon_sessions,
            'summary':summary,'recommendations':trades,'revisions':revisions,'excluded':excluded,
            'warnings':['User-uploaded material cannot establish the original public posting time or identity.',
                        'First-observed timestamps must come from a trustworthy capture to support historical claims.',
                        'SELL scores directional forecasts; it does not simulate margin/borrow/short restrictions.',
                        'Price-only scores are not verified trade performance; no TP/SL, intraday path, liquidity or corporate actions.',
                        'Market dataset provider authenticity and session calendar are not independently certified.']}
