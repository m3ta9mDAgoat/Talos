"""Bounded point-in-time parameter stability diagnostics for offline research.
Not a discovery of alpha, nor a certified CPCV/PBO/DSR implementation.
No external services, broker orders, or untrusted code execution.
"""
from __future__ import annotations
from dataclasses import dataclass
from .contracts import StrategySpec, RiskSpec
from .backtest import backtest, monte_carlo, walk_forward


def candidates(spec: StrategySpec):
    """Deterministic local 3x3 grid, valid only for rule families with discrete parameters."""
    if spec.plugin in ('ema_cross','sma_cross'):
        short_step=max(2,round(spec.short*.2))
        long_step=max(3,round(spec.long*.2))
        variants=[spec.model_copy(update={'short':s,'long':l})
            for s in (max(2,spec.short-short_step),spec.short,min(250,spec.short+short_step))
            for l in (max(3,spec.long-long_step),spec.long,min(500,spec.long+long_step))
            if s < l]
    elif spec.plugin=='rsi_reversion':
        variants=[spec.model_copy(update={'rsi_buy':float(b),'rsi_sell':float(s)})
            for b in (max(1,spec.rsi_buy-4),spec.rsi_buy,min(50,spec.rsi_buy+4))
            for s in (max(50,spec.rsi_sell-4),spec.rsi_sell,min(99,spec.rsi_sell+4))
            if b<s]
    else:
        raise ValueError('Stability grid is supported only for EMA/SMA crossover and RSI; custom plugins need a declared parameter search space')
    seen=set();out=[]
    for v in variants:
        key=(v.short,v.long) if v.plugin!='rsi_reversion' else (v.rsi_buy,v.rsi_sell)
        if key not in seen:seen.add(key);out.append((key,v))
    return out


def inspect(bars:list[dict],strategy:StrategySpec,risk:RiskSpec,*,simulations=25,seed=42):
    """Monte Carlo applied to EVERY enumerated set, plus a single untouched OOS split.
    A per-fold expanding walk forward is chosen on historical training observations
    only; NO winner is selected by OOS results. Diagnostic clusters use IS only.
    """
    if not 5 <= simulations <= 200:raise ValueError('Robustness MC simulations must be between 5 and 200 per parameter set')
    if len(bars)<250:raise ValueError('At least 250 bars required for robustness grid')
    variants=candidates(strategy)
    if len(bars)*simulations*len(variants)>2_000_000:
        raise ValueError('Robustness workload exceeds 2 million bar-simulations; shorten dataset or reduce simulations')
    split=int(len(bars)*.7)
    if split<150 or len(bars)-split<60:raise ValueError('Insufficient IS/OOS split')
    training=bars[:split]
    rows=[]
    for index,(params,candidate) in enumerate(variants):
        inside=backtest(training,candidate,risk,record_equity=False)['metric']
        mc=monte_carlo(training,candidate,risk,n=simulations,seed=seed+index*37)
        # Subsequent OOS values are strictly diagnostic and must NOT choose the winner.
        outside=backtest(bars,candidate,risk,start_index=split,record_equity=False)['metric']
        rows.append({'parameters':{'short':params[0],'long':params[1]} if strategy.plugin!='rsi_reversion'
                    else {'rsi_buy':params[0],'rsi_sell':params[1]},
                    'is':inside,'oos':outside,'monte_carlo':mc,'mc_tested':True,'index':index})
    # Training-only neighborhood graph: top half by total IS return, and 
    # connected in the grid (one coordinate differs by one step).
    returns=sorted(float(row['is'].get('total_return_pct') or 0) for row in rows)
    threshold=returns[len(returns)//2]
    active={i for i,row in enumerate(rows) if float(row['is'].get('total_return_pct') or 0)>=threshold}
    coords=[tuple(row['parameters'].values()) for row in rows]
    xvals=sorted(set(x for x,_ in coords));yvals=sorted(set(y for _,y in coords))
    cell={v:(xvals.index(v[0]),yvals.index(v[1])) for v in coords}
    groups=[]
    while active:
        queue=[min(active)];active.remove(queue[0]);cluster=[]
        while queue:
            current=queue.pop();cluster.append(current)
            a,b=cell[coords[current]]
            near=[j for j in active if sum(abs(k-l) for k,l in zip(cell[coords[j]],(a,b)))==1]
            for j in sorted(near):active.remove(j);queue.append(j)
        groups.append(cluster)
    groups.sort(key=lambda cluster:(-len(cluster),min(cluster)))
    cluster_results=[{'size':len(group),'parameter_indices':sorted(group),
          'mean_is_return_pct':round(sum(float(rows[i]['is'].get('total_return_pct') or 0) for i in group)/len(group),4),
          'mean_oos_return_pct_descriptive_only':round(sum(float(rows[i]['oos'].get('total_return_pct') or 0) for i in group)/len(group),4)}
          for group in groups]
    best_train=max(range(len(rows)),key=lambda i:(float(rows[i]['is'].get('total_return_pct') or -1e12),-i))
    # Existing walk_forward re-selects its local training candidates in each fold.
    wf=walk_forward(bars,variants[best_train][1],risk)
    return {'schema':'talos.research.robustness.v1','status':'RESEARCH_ONLY',
        'sets_evaluated':len(rows),'monte_carlo_per_set':simulations,
        'monte_carlo_runs_total':simulations*len(rows),
        'train_end':bars[split-1]['date'],'oos_start':bars[split]['date'],
        'in_sample_selected_parameter_index':best_train,
        'parameter_sets':rows,'train_only_stability_clusters':cluster_results,
        'walk_forward':wf,
        'limitations':['Exploratory local grid, not exhaustive optimization',
           'OOS reported descriptively and must not be reused for selection',
           'Original walk-forward retunes a limited neighborhood on training only',
           'Synthetic OHLC block bootstrap, not a confidence interval of real trading',
           'No PBO/DSR/CPCV certification; regime changes and data provenance remain risks']}
