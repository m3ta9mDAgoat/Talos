"""Optional actual Talos v2 end-to-end test, enabled by TALOS_RESEARCH_ROOT."""
import os,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from talos_bot.service import BotService
from talos_bot.db import BotDB

class Integration(unittest.TestCase):
    @unittest.skipUnless(os.getenv('TALOS_RESEARCH_ROOT'),'Set TALOS_RESEARCH_ROOT to run real API/worker integration')
    def test_bot_v2_http_contract_and_worker(self):
        root=os.getenv('TALOS_RESEARCH_ROOT');sys.path.insert(0,root)
        from fastapi.testclient import TestClient
        from datetime import date,timedelta
        with tempfile.TemporaryDirectory() as td:
            os.environ['TALOS_LAB_DATABASE_URL']='sqlite:///'+str(Path(td)/'module.sqlite3')
            os.environ['TALOS_LAB_ADMIN_TOKEN']='A'*48
            from server.api import create_app
            from server.storage import Store
            from worker.execute import execute
            store=Store('sqlite:///'+str(Path(td)/'data.sqlite3'))
            app=create_app(store)
            client=TestClient(app)
            class Adapter:
                def get(self,p):
                    r=client.get(p,headers={'Authorization':'Bearer '+'A'*48});r.raise_for_status();return r.json()
                def post(self,p,data):
                    r=client.post(p,json=data,headers={'Authorization':'Bearer '+'A'*48});r.raise_for_status();return r.json()
            class Tg:
                def __init__(self):self.messages=[]
                def send(self,chat,text):self.messages.append((chat,text))
            tg=Tg(); db=BotDB(Path(td)/'bot.sqlite3')
            bot=BotService({'owner_user_ids':[123],'source_chat_ids':[-10042],'notify_job_results':True},db,Adapter(),tg)
            bars=[]
            for i in range(550):
                d=date(2020,1,1)+timedelta(days=i)
                p=100+i*.025
                bars.append({'date':d.isoformat(),'open':p,'high':p+1,'low':p-1,'close':p+.2,'volume':15000})
            d=Adapter().post('/api/v2/research/datasets',{'symbol':'AAPL','bars':bars})
            bot.command(123,123,'/backtest',['ema_cross','AAPL',d['dataset_id']])
            job_id=next(iter(store.list()))['id']
            task=store.claim('test_worker_2026')
            self.assertEqual(task['id'],job_id)
            result=execute(task,dataset_fetch=store.get_dataset,archive_fetch=store.get_archive)
            self.assertEqual(result['status'],'COMPLETED')
            self.assertTrue(store.finish(job_id,'test_worker_2026',result,None))
            bot.notify_tracked()
            self.assertTrue(any('complete' in s for _,s in tg.messages))
            self.assertTrue(Adapter().get('/health')['live_orders_enabled'] is False)
            # Real Telegram-style event ingest -> real immutable Research API archive.
            bot.handle({'update_id':100,'channel_post':{'chat':{'id':-10042,'type':'channel'},'message_id':1,'text':'BUY AAPL'}})
            archive_id=bot.archive_id()
            self.assertEqual(len(store.get_archive(archive_id)),1)
            bot.command(123,123,'/replay',[])
            replay_job=[j for j in store.list() if j['kind']=='telegram_replay'][0]
            task=store.claim('test_worker_2026')
            self.assertEqual(task['id'],replay_job['id'])
            replay=execute(task,dataset_fetch=store.get_dataset,archive_fetch=store.get_archive)
            self.assertEqual(replay['schema'],'talos.research.telegram-replay.v1')
            self.assertTrue(store.finish(task['id'],'test_worker_2026',replay,None))
            db.conn.close()
