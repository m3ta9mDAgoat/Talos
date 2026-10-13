"""UI+server contract tests; no real Telegram or brokerage secrets."""
import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from server.api import create_app
from server.storage import Store

SECRET='admin_test_'+'a'*40
class ConnectedV24(unittest.TestCase):
 def app(self,tmp):
  return create_app(Store('sqlite:///'+str(Path(tmp)/'test.db')))
 def test_bot_heartbeat_and_auth(self):
  with tempfile.TemporaryDirectory() as temp:
   with patch.dict(os.environ,{'TALOS_LAB_ADMIN_TOKEN':SECRET}):
    with TestClient(self.app(temp)) as client:
     self.assertEqual(client.get('/api/v2/integration/status').status_code,401)
     auth={'authorization':'Bearer '+SECRET}
     initial=client.get('/api/v2/integration/status',headers=auth).json()
     self.assertEqual(initial['bot']['status'],'not_connected')
     self.assertFalse(initial['live_orders_enabled'])
     payload={'bot_name':'talos_integration_test','captured_events':12,'archive_id':None}
     self.assertEqual(client.post('/api/v2/integration/bot/heartbeat',json=payload).status_code,401)
     self.assertEqual(client.post('/api/v2/integration/bot/heartbeat',headers=auth,json=payload).status_code,200)
     online=client.get('/api/v2/integration/status',headers=auth).json()['bot']
     self.assertEqual(online['status'],'online')
     self.assertEqual(online['captured_events'],12)
     self.assertEqual(online['bot_name'],'talos_integration_test')
     self.assertNotIn('bot_token',str(online))
     self.assertEqual(client.post('/api/v2/research/live/orders',headers=auth).status_code,403)
 def test_all_html_click_handlers_and_no_demo_nav(self):
  from html.parser import HTMLParser
  s=(Path(__file__).resolve().parents[1]/'web/index.html').read_text()
  class Collect(HTMLParser):
   def __init__(self):super().__init__();self.buttons=[];self.ids=set();self.script=False;self.js=[]
   def handle_starttag(self,tag,attrs):
    d=dict(attrs)
    if tag=='button':self.buttons.append(d)
    if d.get('id'):self.ids.add(d['id'])
    if tag=='script':self.script=True
   def handle_endtag(self,tag):
    if tag=='script':self.script=False
   def handle_data(self,v):
    if self.script:self.js.append(v)
  soup=Collect();soup.feed(s);js=' '.join(soup.js)
  import re
  funcs=set(re.findall(r'\b(?:async\s+)?function\s+([a-zA-Z_$][\w$]*)\s*\(',js))
  for b in soup.buttons:
   x=b.get('onclick')
   if x:
    fn=re.match(r'([a-zA-Z_$][\w$]*)\s*\(',x)
    self.assertIsNotNone(fn, x)
    self.assertIn(fn.group(1),funcs,x)
  for route in ('telegram-view','jobs-view','dataset-view','strategies-view','replay-view'):
   self.assertIn(route,soup.ids)
  self.assertIn('header-connect',soup.ids)
  self.assertNotIn('>Preview mode<',s)
  self.assertNotIn('>DEMO WORKSPACE<',s)
if __name__=='__main__':unittest.main()
