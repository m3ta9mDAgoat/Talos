"""End-to-end smoke using internal authenticated HTTP client with no external services."""
import os,tempfile,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['TALOS_LAB_ADMIN_TOKEN']='adm-'+('x'*50)
os.environ['TALOS_LAB_WORKER_TOKEN']='wrk-'+('y'*50)
from fastapi.testclient import TestClient
from server.api import create_app
from server.storage import Store
from worker.agent import Agent
import httpx
with tempfile.TemporaryDirectory() as d:
    store=Store('sqlite:///'+str(Path(d)/'run.sqlite3'))
    app=create_app(store)
    with TestClient(app) as client:
        admin={'Authorization':'Bearer '+os.environ['TALOS_LAB_ADMIN_TOKEN']}
        h=client.get('/health');assert h.status_code==200
        j=client.post('/api/v2/research/jobs',headers=admin,json={'kind':'research','request':{'engine':'reference','strategy':{'plugin':'ema_cross'},'data':{'source':'sample'},'walk_forward':False,'monte_carlo':5}})
        assert j.status_code==201,j.text
        jid=j.json()['id']
        owner='smokeworker_abcdefghi'
        h={'Authorization':'Bearer '+os.environ['TALOS_LAB_WORKER_TOKEN'],'x-worker-id':owner}
        job=client.post('/api/v2/worker/claim',headers=h).json()['job'];assert job['id']==jid
        from worker.execute import execute
        result=execute(job)
        f=client.post(f'/api/v2/worker/jobs/{jid}/finish',headers=h,json={'owner':owner,'result':result})
        assert f.status_code==200,f.text
        done=client.get('/api/v2/research/jobs/'+jid,headers=admin).json()
        assert done['status']=='complete'
        x=client.get('/api/v2/integration/status',headers=admin).json()
        assert x['live_orders_enabled'] is False
        print('PASS: approved UI /health, authenticated job->worker->backtest->result and integration status')
