"""Offline performance baseline; no broker, no remote API calls."""
from __future__ import annotations
import json,statistics,time,platform,sys,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.market_data import sample_bars
from core.contracts import StrategySpec,RiskSpec
from core.backtest import backtest

def bench(count=9):
    bars=sample_bars(start='2020-01-01',end='2024-12-31')
    spec=StrategySpec(plugin='ema_cross',short=20,long=80)
    times=[]
    for _ in range(count):
        started=time.perf_counter_ns()
        backtest(bars,spec,RiskSpec(),record_equity=False)
        times.append((time.perf_counter_ns()-started)/1e6)
    times.sort()
    return {'schema':'talos.performance.baseline.v1','mode':'OFFLINE_SYNTHETIC_REFERENCE',
            'system':platform.system(),'python':sys.version.split()[0],
            'bars':len(bars),'iterations':count,'median_ms':round(statistics.median(times),3),
            'p95_ms':round(times[min(count-1,int(.95*(count-1)))],3),
            'p99_ms':round(times[min(count-1,int(.99*(count-1)))],3),
            'warning':'Synthetic Python compute timing, not feed-to-order broker latency'}

if __name__=='__main__':
    result=bench();path=ROOT/'outputs'/'performance_baseline.json';path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
