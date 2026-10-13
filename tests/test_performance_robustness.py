import json,sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.market_data import sample_bars
from core.contracts import StrategySpec,RiskSpec,ResearchRequest
from core.robustness import inspect,candidates
from worker.execute import execute
from server.api import create_app
from server.storage import Store
from scripts.benchmark_local import bench


def test_robustness_all_sets_monte_carlo_train_oos():
    bars=sample_bars(start='2021-01-01',end='2023-01-01')
    result=inspect(bars,StrategySpec(plugin='ema_cross',short=10,long=35),RiskSpec(),simulations=5,seed=17)
    assert result['status']=='RESEARCH_ONLY'
    assert result['sets_evaluated']==len(result['parameter_sets'])>=5
    assert result['monte_carlo_runs_total']==5*result['sets_evaluated']
    assert all(row['mc_tested'] and row['monte_carlo']['simulations']==5 for row in result['parameter_sets'])
    assert result['train_end']<result['oos_start']
    assert all(row['oos']['end_equity']>0 for row in result['parameter_sets'])
    assert result['walk_forward']['status']=='COMPLETED'
    assert result==inspect(bars,StrategySpec(plugin='ema_cross',short=10,long=35),RiskSpec(),simulations=5,seed=17)


def test_robustness_rsi_and_invalid_requirements():
    bars=sample_bars(start='2021-01-01',end='2023-01-01')
    x=inspect(bars,StrategySpec(plugin='rsi_reversion'),RiskSpec(),simulations=5)
    assert x['sets_evaluated']==9
    with pytest.raises(ValueError,match='supported only'):
        inspect(bars,StrategySpec(plugin='buy_hold'),RiskSpec(),simulations=5)
    with pytest.raises(ValueError,match='2 million'):
        inspect(bars*40,StrategySpec(plugin='ema_cross'),RiskSpec(),simulations=200)
    with pytest.raises(ValueError):ResearchRequest.model_validate({'strategy':{'plugin':'buy_hold'},'robustness':True})


def test_reference_worker_perf_robustness_and_safe_http(tmp_path,monkeypatch):
    monkeypatch.setenv('TALOS_LAB_ADMIN_TOKEN','A'*40)
    monkeypatch.setenv('TALOS_LAB_WORKER_TOKEN','W'*40)
    app=create_app(Store('sqlite:///'+str(tmp_path/'test.db')))
    cli=TestClient(app);headers={'authorization':'Bearer '+'A'*40}
    assert cli.get('/api/v2/research/performance/capabilities').status_code==401
    assert cli.get('/api/v2/research/performance/capabilities',headers=headers).status_code==200
    body={'kind':'research','request':{'engine':'reference','data':{'source':'sample','start':'2021-01-01','end':'2022-12-31'},
      'strategy':{'plugin':'ema_cross','short':10,'long':35},'monte_carlo':0,'walk_forward':False,
      'robustness':True,'robustness_simulations':5}}
    resp=cli.post('/api/v2/research/jobs',headers=headers,json=body);assert resp.status_code==201,resp.text
    claim=cli.post('/api/v2/worker/claim',headers={'authorization':'Bearer '+'W'*40,'x-worker-id':'worker_robust'}).json()['job']
    data=execute(claim,cache_dir=tmp_path/'cache')
    assert data['robustness']['sets_evaluated']>0
    assert data['performance']['total_worker_compute_ms']>=0
    assert data['live_trading_enabled'] is False
    assert cli.post('/api/v2/worker/jobs/'+claim['id']+'/finish',headers={'authorization':'Bearer '+'W'*40},json={'owner':'worker_robust','result':data}).status_code==200
    assert cli.get('/api/v2/research/jobs/'+claim['id'],headers=headers).json()['result']['robustness']['status']=='RESEARCH_ONLY'
    assert cli.post('/api/v2/research/live/orders',headers=headers,json={}).status_code==403


def test_offline_benchmark_labeled_synthetic():
    report=bench(3)
    assert report['mode']=='OFFLINE_SYNTHETIC_REFERENCE' and report['median_ms']>=0
