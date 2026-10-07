from __future__ import annotations

import itertools
from copy import deepcopy
from typing import Any
from app.db.session import SessionLocal
from app.models.research import OptimizationRunRecord
from app.models.strategy import StrategyConfigRecord
from app.services.strategy_backtest import create_strategy_run, advance_strategy_run


def _values(spec:list[float])->list[float]:
    if len(spec)!=3:return []
    a,b,s=map(float,spec);out=[];x=a
    while x<=b+1e-12 and len(out)<25:
        out.append(round(x,10));x+=s
    return out


def _set(defn:dict[str,Any],key:str,value:Any):
    if key.startswith('condition.'):
        _,idx,field=key.split('.',2);defn.setdefault('conditions',[])[int(idx)][field]=value
    else:defn[key]=value


def create_optimization(strategy_config_id:int,base_config:dict[str,Any],max_trials:int=30)->OptimizationRunRecord:
    with SessionLocal() as db:
        s=db.get(StrategyConfigRecord,strategy_config_id)
        if not s:raise ValueError('Strategy not found')
        params=deepcopy(s.parameters_json or {});space=params.get('optimization_space') or {};keys=list(space);grids=[_values(space[k]) for k in keys]
        combos=[dict(zip(keys,x)) for x in itertools.product(*grids)][:max_trials] if keys else [{}]
        row=OptimizationRunRecord(strategy_config_id=strategy_config_id,status='queued',objective='equity_return/max_drawdown',config_json=base_config,state_json={'candidates':combos,'index':0,'active_run_id':None,'active_strategy_id':None},trials_json=[],best_json={})
        db.add(row);db.commit();db.refresh(row);return row


async def advance_optimization(opt_id:int,run_batch_size:int=20)->OptimizationRunRecord:
    with SessionLocal() as db:
        opt=db.get(OptimizationRunRecord,opt_id)
        if not opt:raise ValueError('Optimization run not found')
        original=db.get(StrategyConfigRecord,opt.strategy_config_id)
        if not original:raise ValueError('Strategy not found')
        state=dict(opt.state_json or {});candidates=list(state.get('candidates') or []);idx=int(state.get('index',0));active=state.get('active_run_id');active_sid=state.get('active_strategy_id')
        opt.status='running';db.commit()
        if idx>=len(candidates):opt.status='complete';opt.progress=1.0;db.commit();db.refresh(opt);return opt
        if not active:
            params=deepcopy(original.parameters_json or {});definition=deepcopy(params.get('definition') or {})
            for k,v in candidates[idx].items():_set(definition,k,v)
            params['definition']=definition;params['_temporary']=True;params['name']=f"OPT {params.get('name',original.strategy_id)} #{idx+1}"
            temp=StrategyConfigRecord(strategy_id=f'opt-{opt.id}-{idx+1}',version=original.version,enabled=False,stage='optimization',parameters_json=params);db.add(temp);db.commit();db.refresh(temp)
            run=create_strategy_run(temp.id,opt.config_json or {});state['active_run_id']=run.id;state['active_strategy_id']=temp.id;opt.state_json=state;db.commit();active=run.id;active_sid=temp.id
    run=await advance_strategy_run(int(active),batch_size=run_batch_size)
    with SessionLocal() as db:
        opt=db.get(OptimizationRunRecord,opt_id);state=dict(opt.state_json or {});candidates=list(state.get('candidates') or []);idx=int(state.get('index',0))
        if run.status=='error':
            trial={'parameters':candidates[idx],'score':-1e99,'summary':run.summary_json or {},'run_id':run.id,'status':'error','error':run.error}
        elif run.status!='complete':
            opt.progress=(idx+(run.progress or 0))/max(1,len(candidates));db.commit();db.refresh(opt);return opt
        else:
            summary=run.summary_json or {};ret=float(summary.get('model_equity_return_pct') or 0);dd=abs(float(summary.get('model_max_drawdown_pct') or 0));score=ret/(dd+1e-9) if dd else ret
            trial={'parameters':candidates[idx],'score':score,'summary':summary,'run_id':run.id,'status':'complete'}
        trials=list(opt.trials_json or []);trials.append(trial);opt.trials_json=trials;best=max(trials,key=lambda x:x.get('score',-1e99));opt.best_json=best
        state['index']=idx+1;state['active_run_id']=None;state['active_strategy_id']=None;opt.state_json=state;opt.progress=(idx+1)/max(1,len(candidates));opt.status='complete' if idx+1>=len(candidates) else 'running';db.commit();db.refresh(opt);return opt
