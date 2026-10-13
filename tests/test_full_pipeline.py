import json,os,sys,time,hashlib
from pathlib import Path
from datetime import datetime,timezone,timedelta
from tempfile import TemporaryDirectory
import pytest
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server.storage import Store,meta
from server.api import create_app
from core.contracts import ResearchRequest,StrategySpec,RiskSpec
from core.market_data import normalize,sample_bars
from core.strategy import signals,trusted_python_signals
from core.backtest import backtest,walk_forward,monte_carlo
from core.telegram import normalize_archive,replay
from core.brokers import PaperBook,LiveBroker
from worker.execute import execute

@pytest.fixture
def harness(monkeypatch,tmp_path):
    monkeypatch.setenv('TALOS_LAB_ADMIN_TOKEN','A'*40)
    monkeypatch.setenv('TALOS_LAB_WORKER_TOKEN','W'*40)
    store=Store('sqlite:///'+str(tmp_path/'lab.db'))
    cli=TestClient(create_app(store))
    return cli,store,{'authorization':'Bearer '+'A'*40},{'authorization':'Bearer '+'W'*40}

def reqdata(plugin='sma_cross'):
    return {'kind':'research','idempotency_key':'research-key-01','request':{
        'engine':'reference','data':{'source':'sample','symbol':'AAPL','start':'2020-01-01','end':'2023-12-31'},
        'strategy':{'plugin':plugin,'short':10,'long':35},'risk':{'initial_cash':10000},
        'walk_forward':True,'monte_carlo':15,'random_seed':11}}

def test_authenticated_complete_durable_research(harness,monkeypatch,tmp_path):
    cli,store,a,w=harness
    data=reqdata()
    assert cli.post('/api/v2/research/jobs',json=data).status_code==401
    assert cli.post('/api/v2/worker/claim',headers=a).status_code==401
    new=cli.post('/api/v2/research/jobs',json=data,headers=a)
    assert new.status_code==201,new.text
    jid=new.json()['id']
    reused=cli.post('/api/v2/research/jobs',json=data,headers=a).json()
    assert reused['id']==jid and reused['reused']
    fail=cli.post('/api/v2/research/jobs',json={**data,'request':{**data['request'],'monte_carlo':9}},headers=a)
    assert fail.status_code==409
    claim=cli.post('/api/v2/worker/claim',headers={**w,'x-worker-id':'worker_AAA'}).json()['job']
    assert claim['id']==jid
    assert cli.post('/api/v2/worker/jobs/'+jid+'/heartbeat',headers=w,json={'owner':'wrong-worker'}).status_code==409
    assert cli.post('/api/v2/worker/jobs/'+jid+'/heartbeat',headers=w,json={'owner':'worker_AAA'}).status_code==200
    got=execute(claim,cache_dir=tmp_path/'cache')
    assert got['status']=='COMPLETED'
    assert len(got['backtest']['equity'])>=1000
    assert got['backtest']['metric']['end_equity']>0
    assert got['monte_carlo']['simulations']==15
    assert got['walk_forward']['status']=='COMPLETED'
    wrong=cli.post('/api/v2/worker/jobs/'+jid+'/finish',headers=w,json={'owner':'worker_wrong','result':got})
    assert wrong.status_code==409
    done=cli.post('/api/v2/worker/jobs/'+jid+'/finish',headers=w,json={'owner':'worker_AAA','result':got})
    assert done.status_code==200
    returned=cli.get('/api/v2/research/jobs/'+jid,headers=a).json()
    assert returned['result']['backtest']['metric']['end_equity']==got['backtest']['metric']['end_equity']
    assert returned['result_sha']
    assert cli.post('/api/v2/worker/jobs/'+jid+'/finish',headers=w,json={'owner':'worker_AAA','result':got}).status_code==409
    assert cli.post('/api/v2/worker/claim',headers={**w,'x-worker-id':'worker_BBB'}).json()['job'] is None
    # Disk-persisted result survives store/server recreation.
    newstore=Store('sqlite:///'+str(tmp_path/'lab.db'))
    assert newstore.get(jid,full=True)['status']=='complete'

def test_cancel_and_isolate_worker(harness):
    cli,store,a,w=harness
    jid=cli.post('/api/v2/research/jobs',json=reqdata(),headers=a).json()['id']
    claim=cli.post('/api/v2/worker/claim',headers={**w,'x-worker-id':'worker_AAA'}).json()['job']
    assert cli.post('/api/v2/research/jobs/'+jid+'/cancel',headers=a).status_code==200
    assert cli.post('/api/v2/worker/jobs/'+jid+'/finish',headers=w,json={'owner':'worker_AAA','result':{'status':'COMPLETED'}}).status_code==409
    assert cli.get('/api/v2/research/jobs/'+jid,headers=a).json()['status']=='cancelled'

def test_market_data_csv_validation_and_worker_access(harness):
    cli,store,a,w=harness
    bars=sample_bars(start='2022-01-01',end='2022-03-01')
    r=cli.post('/api/v2/research/datasets',headers=a,json={'symbol':'FOO','bars':bars})
    assert r.status_code==201,r.text
    ref=r.json()['dataset_id']
    assert cli.get('/api/v2/worker/datasets/'+ref,headers=a).status_code==401
    fetched=cli.get('/api/v2/worker/datasets/'+ref,headers=w).json()
    assert fetched['bars']==normalize(bars)
    damaged=[dict(x) for x in bars];damaged[-1]['high']=damaged[-1]['low']
    assert cli.post('/api/v2/research/datasets',headers=a,json={'symbol':'FOO','bars':damaged}).status_code==422
    task=reqdata()['request'];task['data']={'source':'dataset','dataset_id':ref,'symbol':'FOO','start':'2022-01-01','end':'2022-03-01'}
    result=execute({'kind':'research','request':task},dataset_fetch=store.get_dataset,cache_dir=None)
    assert result['provenance']['source']=='USER_CSV_UNVERIFIED_MARKET_PROVENANCE'

def test_tg_archive_revision_replay_readonly(harness):
    cli,store,a,w=harness
    base={'chat_id':123,'message_id':456,'timestamp':'2024-03-01T10:00:00+00:00','event_type':'new','text':'BUY'}
    edited={**base,'revision':'1','event_type':'edit','timestamp':'2024-03-01T10:05:00+00:00','text':'SELL'}
    deleted={**base,'revision':'2','event_type':'delete','timestamp':'2024-03-01T10:15:00+00:00'}
    r=cli.post('/api/v2/research/telegram/import',headers=a,json={'events':[base,deleted,edited]})
    assert r.status_code==201,r.text
    ref=r.json()['id'];archive=cli.get('/api/v2/worker/telegram/'+ref,headers=w).json()['events']
    result=replay(archive,cutoff='2024-03-01T10:10:00+00:00')
    assert result['active_messages']==1 and result['edits']==1 and result['deletes']==0
    assert replay(archive)['active_messages']==0
    assert r.json()['id']==cli.post('/api/v2/research/telegram/import',headers=a,json={'events':[base,deleted,edited]}).json()['id']
    assert cli.post('/api/v2/research/telegram/import',headers=a,json={'events':[base,base]}).status_code==422

def test_paper_broker_risk_and_kill_persist(harness):
    cli,store,a,w=harness
    bought=cli.post('/api/v2/research/paper/order',headers=a,json={'client_order_id':'paper-001','side':'BUY','symbol':'AAPL','qty':4,'price':100})
    assert bought.status_code==200,bought.text
    assert bought.json()['fill']['execution_type']=='PAPER_CLIENT_QUOTE_NOT_MARKET_VERIFIED'
    assert cli.post('/api/v2/research/paper/order',headers=a,json={'client_order_id':'paper-002','side':'SELL','symbol':'AAPL','qty':5,'price':100}).status_code==422
    assert cli.post('/api/v2/research/paper/order',headers=a,json={'client_order_id':'paper-003','side':'BUY','symbol':'AAPL','qty':100,'price':100}).status_code==422
    assert cli.post('/api/v2/research/paper/kill',headers=a).status_code==200
    assert cli.post('/api/v2/research/paper/order',headers=a,json={'client_order_id':'paper-004','side':'SELL','symbol':'AAPL','qty':1,'price':100}).status_code==422
    assert cli.get('/api/v2/research/paper',headers=a).json()['account']['killed']
    assert cli.post('/api/v2/research/live/orders',headers=a,json={}).status_code==403
    with pytest.raises(PermissionError):LiveBroker().place_order()

def test_market_data_no_dup_future_geometry():
    good=sample_bars(start='2023-01-01',end='2023-02-01')
    assert len(normalize(good,allow_weekends=False))>15
    with pytest.raises(ValueError):normalize(good+[good[-1]])
    bad=[dict(x) for x in good];bad[0]['open']=-1
    with pytest.raises(ValueError):normalize(bad)
    bad=[dict(x) for x in good];bad[0]['high']=bad[0]['low']
    with pytest.raises(ValueError):normalize(bad)

def test_strategy_signal_and_next_open_execution():
    bars=sample_bars(start='2020-01-01',end='2021-12-31')
    risk=RiskSpec(max_drawdown_kill=.99)
    for name in ['sma_cross','ema_cross','rsi_reversion','buy_hold']:
        s=StrategySpec(plugin=name,short=5,long=30)
        signal=signals(bars,s);assert len(signal)==len(bars)
        out=backtest(bars,s,risk)
        lookup={r['date']:i for i,r in enumerate(bars)}
        for fill in out['fills']:
            assert lookup[fill['date']]>lookup[fill['signal_date']]
            assert fill['synthetic_fill']
        assert out['metric']['end_equity']>0
    # Kill switch must never look ahead to tomorrow's open on today's equity.
    kill=RiskSpec(max_drawdown_kill=.01)
    out=backtest(bars,StrategySpec(plugin='buy_hold'),kill)
    assert out['risk_kill_triggered']
    assert all(f['date']>f['signal_date'] for f in out['fills'])

def test_montecarlo_walkforward_deterministic():
    bars=sample_bars(start='2020-01-01',end='2023-12-31')
    s=StrategySpec(short=10,long=40);risk=RiskSpec()
    a=monte_carlo(bars,s,risk,20,42);b=monte_carlo(bars,s,risk,20,42)
    assert a==b
    w=walk_forward(bars,s,risk)
    assert w['status']=='COMPLETED' and len(w['folds'])>=1
    for f in w['folds']:assert f['test_start']>f['train_end']

def test_python_trusted_sha256_pinned(tmp_path):
    root=tmp_path/'trusted';root.mkdir()
    code=b'def signals(bars,params):\n    return [1 for _ in bars]\n'
    (root/'demo.py').write_bytes(code)
    (root/'allowlist.json').write_text(json.dumps({'demo':hashlib.sha256(code).hexdigest()}))
    assert trusted_python_signals([{'close':1}],'demo',{},root)==[1]
    (root/'demo.py').write_text('def signals(bars,params):return [0]*len(bars)')
    with pytest.raises(PermissionError):trusted_python_signals([{'close':1}],'demo',{},root)


def test_native_adapter_fail_closed_without_windows(tmp_path,monkeypatch):
    r=reqdata()['request'];r['engine']='lean_parity';r['strategy']['plugin']='ten_month_sma_fixture';r['data']['source']='native_fixture';r['data']['start']='2012-01-01';r['data']['end']='2022-05-31';r['walk_forward']=False;r['monte_carlo']=0;r['risk']['max_allocation']=0.998;r['risk']['slippage_bps']=0
    with pytest.raises(RuntimeError,match='Windows'):
        execute({'kind':'native_parity','request':r})
    assert ResearchRequest.model_validate(r).engine=='lean_parity'
    # Arbitrary JSON cannot request free-form Python or shell execution.
    r['strategy']['plugin']='arbitrary_python';r['engine']='reference'
    with pytest.raises(Exception):ResearchRequest.model_validate(r)

def test_agent_actual_http_cycle_via_testserver(harness,tmp_path):
    """Exercises full HTTP shape: admin enqueue -> HTTP worker claim -> local execute -> upload."""
    import httpx
    from worker.agent import Agent
    cli,store,a,w=harness
    jobdata=reqdata();jobdata['idempotency_key']='agent-network-cycle'
    jid=cli.post('/api/v2/research/jobs',json=jobdata,headers=a).json()['id']
    def dispatch(request:httpx.Request):
        path=request.url.raw_path.decode()
        res=cli.request(request.method,path,headers=dict(request.headers),content=request.content)
        return httpx.Response(res.status_code,headers=dict(res.headers),content=res.content)
    agent=Agent(url='http://127.0.0.1:8018',token='W'*40,
                owner='worker_integration',transport=httpx.MockTransport(dispatch),poll=1)
    assert agent.once() is True
    state=cli.get('/api/v2/research/jobs/'+jid,headers=a).json()
    assert state['status']=='complete',state
    assert state['result']['schema']=='talos.research.result.v1'
    assert agent.once() is False
    agent.client.close()


def test_telegram_signal_broker_replay(harness,tmp_path):
    cli,store,a,w=harness
    events=[{'chat_id':'c','message_id':'1','timestamp':'2023-01-03T12:00:00+00:00','text':'BUY AAPL'},
            {'chat_id':'c','message_id':'2','timestamp':'2023-01-10T12:00:00+00:00','text':'SELL AAPL'}]
    archiveid=cli.post('/api/v2/research/telegram/import',headers=a,json={'source':'test','events':events}).json()['id']
    request=reqdata()['request']
    request['strategy']={'plugin':'telegram_signal','parameters':{'archive_id':archiveid}}
    request['monte_carlo']=0;request['walk_forward']=False
    request['data']={'source':'sample','symbol':'AAPL','start':'2023-01-01','end':'2023-02-01'}
    jid=cli.post('/api/v2/research/jobs',headers=a,json={'kind':'research','request':request}).json()['id']
    t=cli.post('/api/v2/worker/claim',headers={**w,'x-worker-id':'worker_tel'}).json()['job']
    result=execute(t,archive_fetch=store.get_archive,cache_dir=tmp_path/'cache')
    assert result['telegram_event_audit']['accepted_explicit_commands']==2
    assert [x['side'] for x in result['backtest']['fills']]==['BUY','SELL']
    assert result['backtest']['fills'][0]['date']>'2023-01-03'
    assert result['backtest']['fills'][1]['date']>'2023-01-10'
    assert result['monte_carlo']['status']=='disabled'


def test_trusted_python_end_to_end():
    from core.strategy import signals
    bars=sample_bars(start='2020-01-01',end='2021-12-31')
    s=StrategySpec(plugin='trusted_python',trusted_name='trend',parameters={'lookback':25})
    output=backtest(bars,s,RiskSpec())
    assert output['fills'] and output['metric']['end_equity']>0
    assert all(x['date']>x['signal_date'] for x in output['fills'])


def test_archive_provenance_endpoint(harness):
    cli,store,a,w=harness
    assert cli.get('/api/v2/research/evidence/stage3').status_code==401
    proof=cli.get('/api/v2/research/evidence/stage3',headers=a)
    assert proof.status_code==200
    assert proof.json()['strict_parity_passed'] is True
    assert proof.json()['verified_in_this_environment'] is False
    assert {x['symbol'] for x in proof.json()['assets']}=={'AAPL','MSFT'}


def test_invalid_native_fixture_rejected_early(harness):
    cli,store,a,w=harness
    x=reqdata()
    x['kind']='native_parity';x['request']['engine']='lean_parity'
    assert cli.post('/api/v2/research/jobs',json=x,headers=a).status_code==422

def test_lease_expiry_and_retry_owner_protection(harness):
    from sqlalchemy import update
    from server.storage import job as job_table
    cli,store,a,w=harness
    key=reqdata();key['idempotency_key']='lease-exhaustion'
    jid=cli.post('/api/v2/research/jobs',headers=a,json=key).json()['id']
    # Force lease expiry deterministically (no sleep/time dependence).
    for k in range(3):
        owner='worker_%03d'%k
        claimed=store.claim(owner,seconds=30)
        assert claimed['id']==jid
        assert claimed['attempt']==k+1
        with store.db.begin() as conn:
            conn.execute(update(job_table).where(job_table.c.id==jid).values(lease_until=datetime(2000,1,1,tzinfo=timezone.utc)))
        assert not store.finish(jid,owner,{'invalid':'stale'})
    assert store.claim('worker_new') is None
    assert store.get(jid)['status']=='failed'


def test_paper_account_persist_across_store_instances(harness,tmp_path):
    from server.storage import paper_get
    cli,store,a,w=harness
    cli.post('/api/v2/research/paper/order',headers=a,json={'client_order_id':'paper-msft-1','symbol':'MSFT','side':'BUY','qty':1,'price':70})
    new_store=Store(store.db.url.render_as_string(hide_password=False))
    state=paper_get(new_store)
    assert state['positions']['MSFT']==1
    assert float(state['cash'])<10000


def test_fixed_native_risk_policy_guard(harness):
    cli,store,a,w=harness
    r=reqdata()['request']
    r['engine']='lean_parity';r['strategy']={'plugin':'ten_month_sma_fixture'}
    r['data']={'source':'native_fixture','symbol':'AAPL','start':'2012-01-01','end':'2022-05-31'}
    r['walk_forward']=False;r['monte_carlo']=0
    assert cli.post('/api/v2/research/jobs',headers=a,json={'kind':'native_parity','request':r}).status_code==422
    r['risk']['max_allocation']=0.998;r['risk']['slippage_bps']=0
    good=cli.post('/api/v2/research/jobs',headers=a,json={'kind':'native_parity','request':r})
    assert good.status_code==201,good.text
    # No false native conversion of generic strategy parameters.
    r['strategy']['parameters']={'custom':1}
    assert cli.post('/api/v2/research/jobs',headers=a,json={'kind':'native_parity','request':r}).status_code==422


def test_backend_and_frontend_legacy_patch_scope():
    import zipfile,hashlib
    src=Path('/mnt/data/talos_workspace/legacy_backend')
    patched=Path('/mnt/data/TALOS_v478_Research_Proxy_Backend')
    if not src.exists() or not patched.exists():pytest.skip('Build-time patch diff only')
    originals={str(p.relative_to(src)):hashlib.sha256(p.read_bytes()).hexdigest() for p in src.rglob('*') if p.is_file()}
    changed=[name for name,sha in originals.items() if hashlib.sha256((patched/name).read_bytes()).hexdigest()!=sha]
    assert changed==['backend/app/main.py']
    src=Path('/mnt/data/talos_workspace/legacy_frontend');patched=Path('/mnt/data/TALOS_v478_Research_Proxy_Frontend')
    originals={str(p.relative_to(src)):hashlib.sha256(p.read_bytes()).hexdigest() for p in src.rglob('*') if p.is_file()}
    changed=[name for name,sha in originals.items() if hashlib.sha256((patched/name).read_bytes()).hexdigest()!=sha]
    assert changed==['frontend/src/main.tsx']

def test_paper_duplicate_order_id_safe(harness):
    cli,store,a,w=harness
    request={'client_order_id':'order-unique-123','symbol':'AAPL','side':'BUY','qty':2,'price':101.5}
    first=cli.post('/api/v2/research/paper/order',headers=a,json=request)
    assert first.status_code==200,first.text
    second=cli.post('/api/v2/research/paper/order',headers=a,json=request)
    assert second.status_code==200 and second.json()['reused'] is True
    assert second.json()['state']['positions']['AAPL']==2
    conflict=cli.post('/api/v2/research/paper/order',headers=a,json={**request,'qty':4})
    assert conflict.status_code==422
    assert cli.get('/api/v2/research/paper',headers=a).json()['account']['positions']['AAPL']==2

def test_manual_retry_failed_job_only(harness):
    cli,store,a,w=harness
    jid=cli.post('/api/v2/research/jobs',headers=a,json=reqdata()).json()['id']
    task=cli.post('/api/v2/worker/claim',headers={**w,'x-worker-id':'worker_retry'}).json()['job']
    assert cli.post('/api/v2/worker/jobs/'+jid+'/finish',headers=w,json={'owner':'worker_retry','error':'Test failure'}).status_code==200
    assert cli.get('/api/v2/research/jobs/'+jid,headers=a).json()['status']=='failed'
    assert cli.post('/api/v2/research/jobs/'+jid+'/retry',headers=a).status_code==200
    assert cli.get('/api/v2/research/jobs/'+jid,headers=a).json()['status']=='queued'
    assert cli.post('/api/v2/research/jobs/'+jid+'/retry',headers=a).status_code==409
