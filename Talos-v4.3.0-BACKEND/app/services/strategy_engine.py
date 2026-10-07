from __future__ import annotations

from typing import Any
from app.schemas.backtest import Candle


def sma(values:list[float],i:int,p:int)->float|None:
    if p<=0 or i+1<p:return None
    xs=values[i-p+1:i+1]
    return sum(xs)/p


def ema(values:list[float],i:int,p:int)->float|None:
    if p<=0 or i+1<p:return None
    k=2/(p+1); e=sum(values[:p])/p
    for x in values[p:i+1]: e=x*k+e*(1-k)
    return e


def rsi(values:list[float],i:int,p:int)->float|None:
    if p<=0 or i<p:return None
    gains=[]; losses=[]
    for j in range(i-p+1,i+1):
        d=values[j]-values[j-1]; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/p; al=sum(losses)/p
    if al==0:return 100.0
    return 100-(100/(1+ag/al))


def feature(candles:list[Candle],i:int,spec:dict[str,Any])->float|None:
    name=spec.get('feature','close'); p=int(spec.get('period') or 14); closes=[c.close for c in candles]
    if name=='close': return closes[i]
    if name=='sma': return sma(closes,i,p)
    if name=='ema': return ema(closes,i,p)
    if name=='rsi': return rsi(closes,i,p)
    if name=='return_pct':
        if i<p:return None
        return (closes[i]/closes[i-p]-1)*100
    if name=='volume_ratio':
        av=sma([c.volume for c in candles],i,p)
        return candles[i].volume/av if av else None
    if name=='sma_distance_pct':
        av=sma(closes,i,p)
        return ((closes[i]/av)-1)*100 if av else None
    return None


def compare(left:float|None,op:str,right:float)->bool:
    if left is None:return False
    if op=='<':return left<right
    if op=='<=':return left<=right
    if op=='>':return left>right
    if op=='>=':return left>=right
    if op=='==':return abs(left-right)<1e-12
    return False


def entry_ok(candles:list[Candle],i:int,definition:dict[str,Any])->bool:
    checks=[compare(feature(candles,i,c),str(c.get('op','<')),float(c.get('value',0))) for c in definition.get('conditions') or []]
    if not checks:return False
    return any(checks) if definition.get('entry_logic')=='any' else all(checks)


def simulate_technical(candles:list[Candle],definition:dict[str,Any],fee_bps:float,slippage_bps:float)->list[dict[str,Any]]:
    tp=float(definition.get('take_profit_pct',5)); sl=float(definition.get('stop_loss_pct',2.5)); maxbars=int(definition.get('max_holding_bars',72)); out=[]; i=1
    cost=2*(fee_bps+slippage_bps)/10000
    while i<len(candles)-1:
        if not entry_ok(candles,i,definition):
            i+=1; continue
        ent_i=i+1; entry=candles[ent_i].open*(1+slippage_bps/10000); tp_px=entry*(1+tp/100); sl_px=entry*(1-sl/100); exit_px=None; status='timeout'; j=ent_i
        mfe=0.0; mae=0.0
        for j in range(ent_i,min(len(candles),ent_i+maxbars)):
            c=candles[j]; mfe=max(mfe,(c.high-entry)/entry*100); mae=min(mae,(c.low-entry)/entry*100)
            if c.low<=sl_px: exit_px=sl_px; status='stop'; break
            if c.high>=tp_px: exit_px=tp_px; status='target'; break
        if exit_px is None: exit_px=candles[j].close
        ret=((exit_px/entry)-1-cost)*100
        out.append({'entered_at':candles[ent_i].timestamp,'exited_at':candles[j].timestamp,'entry_price':entry,'exit_price':exit_px,'return_pct':ret,'status':status,'mfe_pct':mfe,'mae_pct':mae})
        i=j+1
    return out
