"""Verifies remote worker presence is AUTHENTICATED and not implied by a deployed UI."""
import os,tempfile,unittest,platform
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from server.api import create_app
from server.storage import Store
from worker.agent import Agent
ADMIN='admin-'+'a'*44
WORKER='worker-'+'b'*44

class ConnectedComponentsV25(unittest.TestCase):
 def test_worker_heartbeat_is_worker_only_and_platform_gated(self):
  with tempfile.TemporaryDirectory() as temp:
   db=Store('sqlite:///'+str(Path(temp)/'state.sqlite3'))
   with patch.dict(os.environ,{'TALOS_MODE':'local','TALOS_LAB_ADMIN_TOKEN':ADMIN,'TALOS_LAB_WORKER_TOKEN':WORKER}):
    with TestClient(create_app(db)) as client:
     url='/api/v2/integration/worker/heartbeat'
     payload={'worker_id':'worker_abcdef12','platform':'Windows','mode':'windows_remote','stage3_harness_detected':False}
     self.assertEqual(client.post(url,json=payload).status_code,401)
     self.assertEqual(client.post(url,headers={'authorization':'Bearer '+ADMIN},json=payload).status_code,401)
     self.assertEqual(client.post(url,headers={'authorization':'Bearer '+WORKER},json={**payload,'platform':'Linux'}).status_code,422)
     status_url='/api/v2/integration/status'
     self.assertEqual(client.get(status_url,headers={'authorization':'Bearer '+ADMIN}).json()['windows_worker']['status'],'not_connected')
     self.assertEqual(client.post(url,headers={'authorization':'Bearer '+WORKER},json=payload).status_code,200)
     status=client.get(status_url,headers={'authorization':'Bearer '+ADMIN}).json()
     self.assertEqual(status['windows_worker']['status'],'online')
     self.assertEqual(status['hosted_reference_worker']['status'],'not_connected')
     hosted={**payload,'worker_id':'hosted_abcd9876','mode':'hosted_reference','platform':'Linux'}
     self.assertEqual(client.post(url,headers={'authorization':'Bearer '+WORKER},json=hosted).status_code,200)
     status=client.get(status_url,headers={'authorization':'Bearer '+ADMIN}).json()
     self.assertEqual(status['hosted_reference_worker']['status'],'online')
     self.assertFalse(status['live_orders_enabled'])
 def test_agent_announce_does_not_claim_linux_as_windows(self):
  with tempfile.TemporaryDirectory() as temp:
   db=Store('sqlite:///'+str(Path(temp)/'state.sqlite3'))
   with patch.dict(os.environ,{'TALOS_MODE':'local','TALOS_LAB_ADMIN_TOKEN':ADMIN,'TALOS_LAB_WORKER_TOKEN':WORKER,'TALOS_LAB_WORKER_MODE':'hosted_reference'}):
    with TestClient(create_app(db)) as client:
     def transport(req):
      response=client.request(req.method,str(req.url).replace('http://127.0.0.1:8018',''),headers=dict(req.headers),content=req.content)
      return httpx.Response(response.status_code,content=response.content,headers=dict(response.headers))
     agent=Agent(url='http://127.0.0.1:8018',token=WORKER,transport=httpx.MockTransport(transport))
     agent.announce()
     result=client.get('/api/v2/integration/status',headers={'authorization':'Bearer '+ADMIN}).json()
     self.assertEqual(result['hosted_reference_worker']['status'],'online')
     self.assertEqual(result['hosted_reference_worker']['platform'],platform.system())
 def test_deployment_version_and_no_live_orders(self):
  with tempfile.TemporaryDirectory() as temp:
   with patch.dict(os.environ,{'TALOS_MODE':'local'}):
    with TestClient(create_app(Store('sqlite:///'+str(Path(temp)/'state.sqlite3')))) as client:
     status=client.get('/health').json()
     self.assertEqual(status['version'],'2.5.0')
     self.assertFalse(status['live_orders_enabled'])
if __name__=='__main__':unittest.main()
