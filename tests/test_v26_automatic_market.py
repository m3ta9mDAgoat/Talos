"""Auto market data: strict vendor validation + durable dataset provenance + worker execution."""
from datetime import date,datetime,timedelta,timezone
from io import BytesIO
from pathlib import Path
import json,os,tempfile,unittest
from unittest.mock import patch
from urllib.error import HTTPError
import httpx
from fastapi.testclient import TestClient
from core.auto_market import yahoo_chart_daily,MarketProviderError
from core.market_data import sample_bars
from server.api import create_app
from server.storage import Store
from worker.agent import Agent

ADMIN='admin_'+'a'*40
WORKER='worker_'+'b'*40

class TestAutoMarketV26(unittest.TestCase):
 def test_yahoo_chart_parser_daily_and_strict_no_synthetic_fallback(self):
  start=date(2023,1,2);end=date(2023,2,10)
  trading_dates=[start+timedelta(days=i) for i in range((end-start).days+1) if (start+timedelta(days=i)).weekday()<5]
  times=[int(datetime(d.year,d.month,d.day,16,tzinfo=timezone.utc).timestamp()) for d in trading_dates]
  bars={k:[] for k in ['open','high','low','close','volume']}
  for i,_ in enumerate(times):
   bars['open'].append(100+i);bars['high'].append(101+i)
   bars['low'].append(99+i);bars['close'].append(100.25+i);bars['volume'].append(900000+i)
  body=json.dumps({'chart':{'error':None,'result':[{'timestamp':times,'indicators':{'quote':[bars]},'events':{}}]}}).encode()
  seen=[]
  def fake_open(req,timeout):
   seen.append((req.full_url,timeout));return BytesIO(body)
  output=yahoo_chart_daily('AAPL',start,end,opener=fake_open)
  self.assertEqual(len(output),len(trading_dates))
  self.assertEqual(output[0]['date'],start.isoformat())
  self.assertEqual(output[-1]['date'],trading_dates[-1].isoformat())
  self.assertIn('query1.finance.yahoo.com/v8/finance/chart/AAPL?',seen[0][0])
  self.assertEqual(seen[0][1],15)
  with self.assertRaises(ValueError):
   yahoo_chart_daily('https://evil.example/x',start,end,opener=fake_open)
  with self.assertRaises(MarketProviderError):
   yahoo_chart_daily('AAPL',start,end,opener=lambda *a,**k: (_ for _ in ()).throw(HTTPError('https://query1.finance.yahoo.com/',429,'Too Many Requests',None,None)))

 def test_auto_fetch_persist_run_and_vendor_down_no_synthetic(self):
  with tempfile.TemporaryDirectory() as td:
   store=Store('sqlite:///'+str(Path(td)/'test.sqlite3'))
   env={'TALOS_LAB_ADMIN_TOKEN':ADMIN,'TALOS_LAB_WORKER_TOKEN':WORKER,
        'TALOS_LAB_WORKER_MODE':'hosted_reference', 'TALOS_MODE':'local',
        'TALOS_LAB_DATA_CACHE':str(Path(td)/'cache')}
   mocked_bars=sample_bars('AAPL','2020-01-01','2020-04-30')
   with patch.dict(os.environ,env), patch('core.auto_market.yahoo_chart_daily',return_value=mocked_bars) as provider:
    with TestClient(create_app(store)) as client:
     admin={'authorization':'Bearer '+ADMIN}
     payload={'symbol':'AAPL','start':'2020-01-01','end':'2020-04-30','provider':'yahoo'}
     self.assertEqual(client.post('/api/v2/research/market-data/fetch',json=payload).status_code,401)
     caps=client.get('/api/v2/research/market-data/capabilities',headers=admin).json()
     self.assertEqual(caps['providers'][0]['status'],'configured_not_guaranteed')
     resp=client.post('/api/v2/research/market-data/fetch',headers=admin,json=payload)
     self.assertEqual(resp.status_code,201,resp.text)
     first=resp.json()
     self.assertFalse(first['synthetic_market_data'])
     self.assertEqual(first['count'],len(mocked_bars))
     self.assertEqual(first['provenance']['source'],'YAHOO_CHART_HISTORICAL_DAILY_RESEARCH')
     self.assertEqual(first['first_date'],'2020-01-01')
     self.assertEqual(store.get_dataset(first['dataset_id'])['provenance']['source'],'YAHOO_CHART_HISTORICAL_DAILY_RESEARCH')
     listing=client.get('/api/v2/research/datasets',headers=admin).json()
     self.assertEqual(listing[0]['provenance']['source'],'YAHOO_CHART_HISTORICAL_DAILY_RESEARCH')
     again=client.post('/api/v2/research/market-data/fetch',headers=admin,json=payload)
     self.assertEqual(again.status_code,201,again.text)
     self.assertEqual(again.json()['dataset_id'],first['dataset_id'])
     provider.assert_called_once()  # on-disk TTL cache reuses vendor observation
     request={'kind':'research','request':{'engine':'reference',
       'data':{'source':'dataset','dataset_id':first['dataset_id'],'symbol':'AAPL',
               'start':'2020-01-01','end':'2020-04-30'},
       'strategy':{'plugin':'sma_cross','short':5,'long':20},
       'walk_forward':False,'monte_carlo':0}}
     queued=client.post('/api/v2/research/jobs',headers=admin,json=request)
     self.assertEqual(queued.status_code,201,queued.text)
     def transport(req):
      response=client.request(req.method,str(req.url).replace('http://127.0.0.1:8018',''),
                              headers=dict(req.headers),content=req.content)
      return httpx.Response(response.status_code,content=response.content,headers=dict(response.headers))
     agent=Agent(url='http://127.0.0.1:8018',token=WORKER,transport=httpx.MockTransport(transport))
     self.assertTrue(agent.once())
     job=client.get('/api/v2/research/jobs/'+queued.json()['id'],headers=admin).json()
     self.assertEqual(job['status'],'complete',job.get('error'))
     self.assertEqual(job['result']['provenance']['source'],'YAHOO_CHART_HISTORICAL_DAILY_RESEARCH')
     self.assertEqual(job['result']['provenance']['dataset_id'],first['dataset_id'])
     self.assertFalse(job['result']['live_trading_enabled'])

   # Fail on external provider outage; never fake returns or persist fake data.
   with patch.dict(os.environ,{'TALOS_LAB_ADMIN_TOKEN':ADMIN,'TALOS_LAB_WORKER_TOKEN':WORKER,
                                'TALOS_MODE':'local','TALOS_LAB_DATA_CACHE':str(Path(td)/'offline')}), \
        patch('core.auto_market.yahoo_chart_daily',side_effect=MarketProviderError('Yahoo upstream 429')):
    with TestClient(create_app(store)) as client:
     before=len(store.list_datasets())
     resp=client.post('/api/v2/research/market-data/fetch',headers={'authorization':'Bearer '+ADMIN},
                      json={'symbol':'MSFT','start':'2020-01-01','end':'2020-04-30'})
     self.assertEqual(resp.status_code,502,resp.text)
     self.assertEqual(len(store.list_datasets()),before)

 def test_ui_wires_market_fetch_without_file(self):
  html=(Path(__file__).resolve().parents[1]/'web/index.html').read_text(encoding='utf-8')
  self.assertIn('id="market-fetch-btn"',html)
  self.assertIn('value="market_auto"',html)
  self.assertIn('fetchLibraryMarketData()',html)
  self.assertIn("ds=await acquireTalosMarketData",html)
  self.assertIn('if(file){',html)
  self.assertNotIn('Select historical OHLCV CSV first.',html)
  self.assertNotIn('Yahoo (not enabled on hosted worker)',html)

if __name__=='__main__':unittest.main()
