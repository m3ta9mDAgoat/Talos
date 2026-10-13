"""Transparent next-session-open reference backtester.
No lookahead: target is computed on close of bar i-1, executed at open of bar i.
Yahoo bars are split-adjusted globally; split count is not applied again.
This reference simulator is NOT native LEAN or NautilusTrader execution.
"""
from __future__ import annotations
from datetime import date
import math,random,statistics
from .contracts import RiskSpec,StrategySpec
from .strategy import signals

def metrics(equity, dates=None):
    if len(equity)<2:return {'end_equity':equity[-1] if equity else None}
    returns=[equity[i]/equity[i-1]-1 for i in range(1,len(equity)) if equity[i-1]>0]
    peak=equity[0];dd=0
    for e in equity:
        peak=max(peak,e);dd=min(dd,e/peak-1)
    std=statistics.stdev(returns) if len(returns)>2 else 0
    years=(date.fromisoformat(dates[-1])-date.fromisoformat(dates[0])).days/365.2425 if dates else (len(equity)-1)/252
    observations_per_year=((len(equity)-1)/years) if years>0 else 252
    sharpe=((statistics.mean(returns)*math.sqrt(observations_per_year)/std) if std>1e-10 else None)
    cagr=(equity[-1]/equity[0])**(1/years)-1 if equity[0]>0 and equity[-1]>0 else None
    return {'end_equity':round(equity[-1],4),'total_return_pct':round((equity[-1]/equity[0]-1)*100,4),
            'cagr_pct':round(cagr*100,4) if cagr is not None else None,
            'max_drawdown_pct':round(dd*100,4),'sharpe':round(sharpe,4) if sharpe is not None else None}

def backtest(bars,strategy:StrategySpec,risk:RiskSpec,*,start_index=0,record_equity=True,signal_override=None):
    if len(bars)<10:raise ValueError('At least 10 daily bars required')
    if len(bars)>40000:raise ValueError('Too many bars')
    if not isinstance(start_index,int) or start_index<0 or start_index>=len(bars):raise ValueError('Invalid start_index')
    target=list(signal_override) if signal_override is not None else signals(bars,strategy)
    if len(target)!=len(bars) or any(x not in (0,1) for x in target):raise ValueError('Invalid signal timeline')
    cash=float(risk.initial_cash);shares=0;peak=cash;locked=False
    fee=risk.fee_bps/10000;slip=risk.slippage_bps/10000
    fills=[];curve=[];equity=[];holdings=[]
    for i,b in enumerate(bars):
        if i<start_index:continue  # Warm-up prices determine signal only; ZERO train-period trades/capital carry-over.
        # Pending signal is from yesterday's close, never today's close.
        desired=bool(target[i-1]) if i>0 else False
        op=b['open'];close=b['close']
        if i>0:
            if locked and shares:
                px=op*(1-slip);q=shares;notional=q*px;commission=notional*fee
                cash+=notional-commission;shares=0
                fills.append({'date':b['date'],'signal_date':bars[i-1]['date'],'side':'SELL_KILL','quantity':q,
                              'fill_price':round(px,6),'commission':round(commission,6),'synthetic_fill':True})
            elif not locked and desired and not shares:
                px=op*(1+slip)
                q=int(min(cash*risk.max_allocation,risk.max_trade_notional)/(px*(1+fee)))
                if q>0:
                    notional=q*px;commission=notional*fee
                    assert notional+commission <= cash+1e-7
                    cash-=notional+commission;shares+=q
                    fills.append({'date':b['date'],'signal_date':bars[i-1]['date'],'side':'BUY','quantity':q,
                                  'fill_price':round(px,6),'commission':round(commission,6),'synthetic_fill':True})
            elif not locked and shares and not desired:
                px=op*(1-slip);q=shares;notional=q*px;commission=notional*fee
                cash+=notional-commission;shares=0
                fills.append({'date':b['date'],'signal_date':bars[i-1]['date'],'side':'SELL','quantity':q,
                              'fill_price':round(px,6),'commission':round(commission,6),'synthetic_fill':True})
        # Yahoo OHLC feed is split-adjusted in its historical unit; do not double split shares.
        # Dividend paydate timing is not modeled. Historic adjusted price dividend effects
        # require further accounting; do not claim native production fidelity.
        value=cash+shares*close;peak=max(peak,value)
        if peak and (peak-value)/peak>=risk.max_drawdown_kill and not locked:
            locked=True
            # Kill executes at next open, not current close: desired sell enforced next bar.
        holdings.append(shares)
        equity.append(value)
        if record_equity:curve.append({'date':b['date'],'equity':round(value,4),'shares':shares})
    analysis_equity=([float(risk.initial_cash)]+equity if start_index else equity)
    analysis_dates=([bars[start_index-1]['date']]+[b['date'] for b in bars[start_index:]] if start_index else [b['date'] for b in bars])
    return {'metric':metrics(analysis_equity,analysis_dates),'fills':fills,'equity':curve,'risk_kill_triggered':locked,
            'bars':len(bars),'engine':'TALOS_REFERENCE_NOT_NATIVE','lookahead_free_orders':True}

def walk_forward(bars,spec:StrategySpec,risk:RiskSpec):
    """Expanding-train / held-out-test, with selection ONLY on training history."""
    n=len(bars);folds=[]
    if n<500:return {'status':'insufficient_bars','folds':[]}
    train_end=int(n*.55);test_len=max(60,int(n*.15))
    for k in range(3):
        if train_end+test_len>n:break
        candidates=[]
        if spec.plugin in ('sma_cross','ema_cross'):
            for x in [(spec.short,spec.long),(max(2,spec.short-3),spec.long),(spec.short,min(500,spec.long+10))]:
                if x[0]<x[1] and x not in candidates:candidates.append(x)
        else:candidates=[(spec.short,spec.long)]
        scores=[]
        for short,long in candidates:
            candidate=spec.model_copy(update={'short':short,'long':long})
            res=backtest(bars[:train_end],candidate,risk,record_equity=False)
            score=res['metric']['total_return_pct'] or -999999
            scores.append((score,candidate))
        _,best=max(scores,key=lambda x:x[0]);upto=train_end+test_len
        evaluation=backtest(bars[:upto],best,risk,start_index=train_end)
        # Fresh 10k test capital; selected parameters see only historical bars,
        # and all actual fills begin at the first OOS day open, never during train.
        fold_metric=evaluation['metric']
        folds.append({'train_end':bars[train_end-1]['date'],'test_start':bars[train_end]['date'],
                      'test_end':bars[upto-1]['date'],'selected_short':best.short,'selected_long':best.long,
                      'heldout':fold_metric,'test_fills':len(evaluation['fills'])})
        train_end+=test_len
    return {'status':'COMPLETED','folds':folds,'overfitting_warning':'Training selection does not predict future profitability'}

def monte_carlo(bars,spec:StrategySpec,risk:RiskSpec,n=200,seed=42):
    """Block-bootstrap daily changes. Path is synthetic, retains per-bar OHLC relative geometry.
    Bootstrap is conditional on historical regime and not independent validation.
    """
    if n<=0:return {'status':'disabled','simulations':0}
    if len(bars)<60:return {'status':'insufficient_bars','simulations':0}
    rng=random.Random(seed);changes=[]
    for i in range(1,len(bars)):
        before=bars[i-1]['close'];now=bars[i]
        changes.append((now['close']/before, now['open']/now['close'],now['high']/now['close'],now['low']/now['close']))
    ends=[];dds=[];N=len(bars);L=10
    for trial in range(n):
        price=bars[0]['close'];generated=[dict(bars[0])];index=1
        while index<N:
            offset=rng.randrange(0,max(1,len(changes)-L+1))
            for j in range(offset,min(offset+L,len(changes))):
                if index>=N:break
                r,oratio,hratio,lratio=changes[j];price=max(1e-6,price*r)
                candidate={'date':bars[index]['date'],'close':price,'open':price*oratio,
                           'high':price*hratio,'low':price*lratio,'volume':bars[index]['volume'],
                           'dividend':0,'split':0}
                candidate['high']=max(candidate['high'],candidate['open'],candidate['close'])
                candidate['low']=min(candidate['low'],candidate['open'],candidate['close'])
                generated.append(candidate);index+=1
        result=backtest(generated,spec,risk,record_equity=False)
        ends.append(result['metric']['total_return_pct']);dds.append(result['metric']['max_drawdown_pct'])
    ends.sort();dds.sort()
    q=lambda v,p:v[max(0,min(len(v)-1,int((len(v)-1)*p)))]
    return {'status':'COMPLETED','simulations':n,'block_days':L,'seed':seed,'return_pct_p05':round(q(ends,.05),3),
            'return_pct_p50':round(q(ends,.5),3),'return_pct_p95':round(q(ends,.95),3),
            'drawdown_pct_p05':round(q(dds,.05),3),'drawdown_pct_p50':round(q(dds,.5),3),
            'loss_probability':round(sum(x<0 for x in ends)/n,4),
            'model':'Synthetic daily block bootstrap; does not validate out-of-sample actual market performance'}
