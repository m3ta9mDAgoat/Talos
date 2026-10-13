"""Deterministic reference candidate: price trend + point-in-time sentiment filter.

No LLM, downloading, lookahead, trading or claim of predictive efficacy.
Signals use EMA at previous close and articles FIRST OBSERVED by previous close.
They may be acted on only at the NEXT trading session open. Outputs research
comparisons against trend-only to test whether sentiment adds value.
"""
from __future__ import annotations
from bisect import bisect_right
from datetime import datetime,timezone
from hashlib import sha256
from pydantic import BaseModel,Field,field_validator
from typing import Literal
import json,math
from .market_data import normalize

class SentimentObservation(BaseModel):
    source:Literal['telegram','rss','x','website','other']
    post_id:str=Field(min_length=1,max_length=180)
    first_observed_at:datetime
    symbol:str=Field(min_length=1,max_length=32)
    score:float=Field(ge=-1,le=1)
    model:str=Field(min_length=1,max_length=120)
    model_version:str=Field(min_length=1,max_length=120)
    @field_validator('first_observed_at')
    @classmethod
    def aware(cls,v):
        if v.utcoffset() is None:raise ValueError('Observed timestamp requires timezone')
        return v.astimezone(timezone.utc)

class TrendSentimentRequest(BaseModel):
    symbol:str=Field(min_length=1,max_length=32)
    dataset_id:str=Field(min_length=64,max_length=64)
    fast_ema:int=Field(default=20,ge=2,le=200)
    slow_ema:int=Field(default=80,ge=3,le=500)
    lookback_sessions:int=Field(default=3,ge=1,le=30)
    sentiment_threshold:float=Field(default=0.15,ge=0,le=1)
    minimum_articles:int=Field(default=2,ge=1,le=100)
    fee_bps:float=Field(default=10,ge=0,le=500)
    slippage_bps:float=Field(default=2,ge=0,le=300)
    observations:list[SentimentObservation]=Field(min_length=1,max_length=3000)


def ema(values,n):
    alpha=2/(n+1);value=None;out=[]
    for v in values:
        value=v if value is None else alpha*v+(1-alpha)*value
        out.append(value)
    return out


def evaluate(req:TrendSentimentRequest,dataset:dict)->dict:
    if req.fast_ema>=req.slow_ema:raise ValueError('fast_ema must be < slow_ema')
    if dataset['symbol'].upper()!=req.symbol.upper():raise ValueError('Dataset symbol mismatch')
    bars=normalize(dataset['bars'])
    if len(bars)<req.slow_ema+25:raise ValueError('Insufficient bars for EMA warmup')
    dates=[b['date'] for b in bars]
    close=[b['close'] for b in bars]
    fast=ema(close,req.fast_ema);slow=ema(close,req.slow_ema)
    # Observation maps to the LAST market session prior to its first_observed calendar day;
    # articles after the day's close are intentionally delayed at least one full day.
    events_by_day={}
    seen=set()
    for o in sorted(req.observations,key=lambda x:(x.first_observed_at,x.post_id)):
        if o.symbol.upper()!=req.symbol.upper():continue
        key=(o.source,o.post_id)
        if key in seen:continue
        seen.add(key)
        idx=bisect_right(dates,o.first_observed_at.date().isoformat())
        if idx<len(bars):events_by_day.setdefault(idx,[]).append({'score':o.score,'seen':o.first_observed_at.isoformat(),
            'source':o.source,'model':o.model,'version':o.model_version})
    # Each indexed score becomes available no earlier than the next bar's OPEN;
    # to compute a signal at prior CLOSE, consume only indices < i.
    def simulation(gated):
        cash=10000.0;shares=0.0; trades=[];equity=[];decisions=[]
        for i,b in enumerate(bars):
            scores=[e['score'] for j in range(max(0,i-req.lookback_sessions),i)
                    for e in events_by_day.get(j,[])]
            pos_trend=(i>req.slow_ema+1 and fast[i-1]>slow[i-1])
            sentiment=(sum(scores)/len(scores)) if scores else None
            eligible=(len(scores)>=req.minimum_articles and sentiment is not None and sentiment>=req.sentiment_threshold)
            target=pos_trend and (eligible if gated else True)
            if target and shares==0:
                execution=b['open']*(1+req.slippage_bps/10000)
                count=math.floor(cash*0.998/(execution*(1+req.fee_bps/10000)))
                if count>0:
                    cash-=count*execution*(1+req.fee_bps/10000);shares=float(count)
                    trades.append({'date':b['date'],'side':'BUY','quantity':count,'execution':execution})
            elif not target and shares>0:
                execution=b['open']*(1-req.slippage_bps/10000)
                cash+=shares*execution*(1-req.fee_bps/10000)
                trades.append({'date':b['date'],'side':'SELL','quantity':int(shares),'execution':execution});shares=0
            eq=cash+shares*b['close']
            equity.append({'date':b['date'],'equity':round(eq,4)})
            if i>=req.slow_ema+2 and (i%10==0 or target):
                decisions.append({'date':b['date'],'previous_close':dates[i-1],
                                  'trend_at_prev_close':pos_trend,'eligible_sentiment_at_prev_close':eligible,
                                  'article_count':len(scores),'sentiment_mean':round(sentiment,4) if sentiment is not None else None,
                                  'target_position':target})
        start=equity[req.slow_ema+2]['equity'];ending=equity[-1]['equity']
        peak=start;mdd=0
        for p in equity[req.slow_ema+2:]:
            peak=max(peak,p['equity']);mdd=min(mdd,p['equity']/peak-1)
        return {'ending_equity':round(ending,2),'max_drawdown_pct':round(mdd*100,4),
                'return_pct_from_oos_start':round((ending/start-1)*100,4),
                'fills':trades,'decisions':decisions[-1000:], 'equity':equity[::max(1,len(equity)//1000)]}
    baseline=simulation(False);filtered=simulation(True)
    return {'schema':'talos.trend_sentiment.reference.v1','status':'COMPLETED_REFERENCE_ONLY',
            'source_authenticated':False,'market_data_verified':False,
            'native_engine_execution':False,'live_orders_enabled':False,
            'symbol':req.symbol,'dataset_id':req.dataset_id,
            'strategy':'ema_trend_filter_plus_sentiment_confirmation',
            'model_cautions':['Only numeric precomputed sentiment scores are consumed; NO LLM inference is run.',
              'Price trend and observations require timestamps; article observations are delayed conservatively by full bar.',
              'Daily close and next open execution only; no paper/live orders; split and dividend accounting not certified.',
              'Compare against trend only and out-of-sample. Positive results do not establish predictive performance.'],
            'trend_only_baseline':baseline,'sentiment_confirmed':filtered,
            'observations_count':len(req.observations),
            'observations_digest':sha256(json.dumps([x.model_dump(mode='json') for x in req.observations],sort_keys=True).encode()).hexdigest()}
