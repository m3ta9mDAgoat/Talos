import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from server.storage import Store
from server.api import create_app
from core.contracts import JobSubmit
from worker.agent import Agent

ADMIN='admin_test_'+'a'*40
WORKER='worker_test_'+'b'*40

class HostedReferenceV23Tests(unittest.TestCase):
 def test_catalog_and_worker_end_to_end(self):
  with tempfile.TemporaryDirectory() as td:
   db=Store('sqlite:///'+str(Path(td)/'web.sqlite'))
   env={'TALOS_LAB_ADMIN_TOKEN':ADMIN,'TALOS_LAB_WORKER_TOKEN':WORKER,
        'TALOS_LAB_WORKER_MODE':'hosted_reference','TALOS_MODE':'local'}
   with patch.dict(os.environ,env):
    with TestClient(create_app(db)) as client:
     self.assertEqual(client.get('/api/v2/research/datasets').status_code,401)
     admin={'authorization':'Bearer '+ADMIN}
     self.assertEqual(client.get('/api/v2/research/datasets',headers=admin).json(),[])
     self.assertEqual(client.get('/api/v2/research/archives',headers=admin).json(),[])
     native=db.submit('native_parity',{'engine':'lean_parity'})[0]
     job=client.post('/api/v2/research/jobs',headers=admin,json={
       'kind':'research','request':{'engine':'reference','data':{'source':'sample','symbol':'AAPL',
       'start':'2022-01-01','end':'2022-12-31'},'strategy':{'plugin':'sma_cross','short':5,'long':20},
       'walk_forward':False,'monte_carlo':3}})
     self.assertEqual(job.status_code,201,job.text)
     def transport_fn(req):
      resp=client.request(req.method,str(req.url).replace('http://127.0.0.1:8018',''),
                  headers=dict(req.headers),content=req.content)
      return httpx.Response(resp.status_code,content=resp.content,headers=dict(resp.headers))
     agent=Agent(url='http://127.0.0.1:8018',token=WORKER,transport=httpx.MockTransport(transport_fn))
     self.assertTrue(agent.once())
     self.assertEqual(client.get('/api/v2/research/jobs/'+job.json()['id'],headers=admin).json()['status'],'complete')
     self.assertEqual(db.get(native['id'])['status'],'queued','Native fixture must remain for Windows')
     self.assertFalse(agent.once())
     self.assertEqual(db.get(native['id'])['status'],'queued')

 def test_dataset_catalog(self):
  with tempfile.TemporaryDirectory() as td:
   db=Store('sqlite:///'+str(Path(td)/'web.sqlite'))
   with patch.dict(os.environ,{'TALOS_LAB_ADMIN_TOKEN':ADMIN,'TALOS_LAB_WORKER_TOKEN':WORKER}):
    with TestClient(create_app(db)) as client:
     rows=[{'date':f'2023-01-{i:02d}','open':100,'high':110,'low':90,'close':101,'volume':500} for i in range(1,12)]
     resp=client.post('/api/v2/research/datasets',json={'symbol':'AAPL','bars':rows},headers={'authorization':'Bearer '+ADMIN})
     self.assertEqual(resp.status_code,201,resp.text)
     dataset_id=resp.json()['dataset_id']
     saved=client.get('/api/v2/research/datasets',headers={'authorization':'Bearer '+ADMIN}).json()
     self.assertEqual(saved[0]['dataset_id'],dataset_id)
     self.assertEqual(saved[0]['bars'],11)

 def test_docker_worker_present_and_ui_pages(self):
  root=Path(__file__).resolve().parents[1]
  docker=(root/'Dockerfile').read_text()
  html=(root/'web'/'index.html').read_text()
  self.assertIn('COPY worker/ ./worker/',docker)
  self.assertIn('TALOS_HOSTED_REFERENCE_WORKER=1',docker)
  for label in ['dataset-view','jobs-view','portfolio-view','deployments-view']:
   self.assertIn('id="'+label+'"',html)

if __name__=='__main__':unittest.main()
