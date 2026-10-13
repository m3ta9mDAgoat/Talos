import base64,json,os,tempfile,unittest,sys
from pathlib import Path
from datetime import datetime,timedelta,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from talos_bot.service import BotService
from talos_bot.db import BotDB
from talos_bot.api import JsonClient,TelegramClient
from talos_bot.runner import load_config

class DummyTelegram:
    def __init__(self):self.sent=[];self.docs={}
    def send(self,chat,text):self.sent.append((chat,text))
    def file_bytes(self,file_id,max_size=1_500_000):return self.docs[file_id]

class FakeTalos:
    def __init__(self):self.calls=[];self.jobs={};self.archive={}
    def get(self,path):
        self.calls.append(('GET',path,None))
        if path=='/health':return {'version':'2.0.0','status':'ok'}
        if path.endswith('/catalog'):return {'plugins':[{'id':'ema_cross'},{'id':'buy_hold'}]}
        if path.endswith('/jobs'):return list(self.jobs.values())
        if path.startswith('/api/v2/research/jobs/'):
            return self.jobs[path.split('/')[-1]]
        if path.endswith('/paper'):return {'account':{'equity':10000}}
        raise KeyError(path)
    def post(self,path,data):
        self.calls.append(('POST',path,data))
        if path.endswith('/telegram/import'):
            import hashlib
            key=hashlib.sha256(json.dumps(data['events'],sort_keys=True).encode()).hexdigest()
            self.archive[key]=data['events'];return {'id':key,'imported_count':len(data['events'])}
        if path.endswith('/jobs'):
            jid='123e4567-e89b-12d3-a456-426614174000'
            self.jobs[jid]={'id':jid,'status':'queued','kind':data['kind'],'result':None};return self.jobs[jid]
        if path.endswith('/verify'):
            return {'summary':{'recommendations_scored':1,'wins':1,'losses':0,'accuracy_pct_of_scored':100}}
        if path.endswith('/phanes/import'):
            return {'archive_id':'f'*64,'new_records_count':4}
        if path.endswith('/cancel'):
            return {'status':'cancelled'}
        raise KeyError(path)

class BotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=BotDB(Path(self.tmp.name)/'bot.sqlite')
        self.cfg={'owner_user_ids':[123],'source_chat_ids':[-10042],'notify_job_results':True}
        self.telegram=DummyTelegram();self.talos=FakeTalos()
        self.bot=BotService(self.cfg,self.db,self.talos,self.telegram)
    def tearDown(self):self.db.conn.close();self.tmp.cleanup()
    def user(self,command,userid=123,update_id=8,document=None,forward=False):
        msg={'chat':{'type':'private','id':userid},'from':{'id':userid},'message_id':update_id}
        if command:msg['text']=command
        if document:msg['document']=document
        if forward:msg['forward_origin']={'type':'channel','chat':{'id':-10042,'title':'Channel A'}}
        return {'update_id':update_id,'message':msg}
    def post(self,update_id=20,text='BUY AAPL',edited=False,chat_id=-10042):
        return {'update_id':update_id,('edited_channel_post' if edited else 'channel_post'):{
           'chat':{'type':'channel','id':chat_id,'title':'Channel A'},'message_id':333,'date':1721122000,'text':text}}
    def test_unauthorized_rejected_except_id(self):
        self.bot.handle(self.user('/jobs',userid=999))
        self.assertEqual(len(self.telegram.sent),0)
        self.bot.handle(self.user('/id',userid=999))
        self.assertIn('999',self.telegram.sent[-1][1])
        self.assertFalse(self.talos.calls)
    def test_channel_authorized_and_edit_point_in_time(self):
        self.bot.handle(self.post())
        self.bot.handle(self.post(21,'SELL AAPL',edited=True))
        self.bot.handle(self.post(20))
        self.bot.handle(self.post(22,chat_id=-999))
        self.assertEqual(self.db.count(),2)
        data=self.db.last_events()
        self.assertEqual([x['event_type'] for x in data],['new','edit'])
        self.assertNotIn('2024-',data[0]['timestamp'])
        self.assertEqual(data[0]['claimed_published_at'],1721122000)
    def test_sync_archive_idempotent(self):
        self.bot.handle(self.post())
        self.bot.handle(self.user('/sync'))
        arch=self.db.meta('archive_id');self.assertEqual(len(arch),64)
        self.bot.handle(self.user('/sync'))
        self.assertEqual(self.db.meta('archive_id'),arch)
        self.assertEqual(len(self.talos.archive),1)
    def test_job_replay_stays_paper_and_reference(self):
        self.bot.handle(self.post())
        self.bot.handle(self.user('/replay'))
        body=[x[2] for x in self.talos.calls if x[1].endswith('/jobs') and x[0]=='POST'][-1]
        self.assertEqual(body['kind'],'telegram_replay')
        self.assertEqual(body['request']['strategy']['plugin'],'telegram_signal')
        self.assertEqual(body['request']['monte_carlo'],0)
        self.assertEqual(body['request']['walk_forward'],False)
    def test_backtest_requires_dataset_and_safe_reference(self):
        self.bot.handle(self.user('/backtest ema_cross AAPL '+('a'*64)))
        body=[x[2] for x in self.talos.calls if x[0]=='POST'][-1]
        self.assertEqual(body['request']['data']['source'],'dataset')
        self.assertEqual(body['request']['engine'],'reference')
        self.bot.handle(self.user('/backtest risk_free_profit AAPL '+('a'*64)))
        self.assertIn('Allowed:',self.telegram.sent[-1][1])
    def test_verify_future_safe_contract(self):
        self.bot.handle(self.post())
        self.bot.handle(self.post(21,'SELL AAPL',edited=True))
        self.bot.handle(self.user('/verify AAPL '+('b'*64)))
        b=[x[2] for x in self.talos.calls if x[1].endswith('/verify')][-1]
        self.assertEqual(b['events'][0]['event_type'],'new')
        self.assertEqual(b['events'][1]['event_type'],'edit')
        self.assertNotIn('claimed_published_at',b['events'][0])
        self.assertEqual(b['source_kind'],'telegram')
    def test_phanes_document(self):
        self.telegram.docs['doc1']=b'PKzip-bytes-stub'
        self.bot.handle(self.user('',document={'file_id':'doc1','file_name':'Phanes.xlsx','file_size':15}))
        b=[x[2] for x in self.talos.calls if x[1].endswith('phanes/import')][-1]
        self.assertEqual(base64.b64decode(b['xlsx_base64']),b'PKzip-bytes-stub')
        self.assertNotIn('live',str(b))
    def test_job_terminal_once(self):
        self.bot.handle(self.user('/backtest ema_cross AAPL '+('a'*64)))
        jid=next(iter(self.talos.jobs))
        self.talos.jobs[jid]['status']='complete'
        before=len(self.telegram.sent);self.bot.notify_tracked();self.bot.notify_tracked()
        self.assertEqual(len(self.telegram.sent)-before,1)
    def test_no_channel_no_capture(self):
        self.bot.cfg['source_chat_ids']=[];self.bot.source_ids=set()
        self.bot.handle(self.post())
        self.assertEqual(self.db.count(),0)
    def test_no_external_unencrypted_api(self):
        with self.assertRaises(ValueError):JsonClient('http://example.com',token='x')
    def test_config_ids_reject_strings(self):
        p=Path(self.tmp.name)/'cfg.json';p.write_text(json.dumps({
            'bot_token':'1:'+'a'*40,'owner_user_ids':['123'],'source_chat_ids':[],'talos_url':'http://127.0.0.1:8018'}))
        with self.assertRaises(ValueError):load_config(p)
    def test_worker_not_required_for_simple_reply(self):
        self.bot.handle(self.user('/start'))
        self.assertIn('LIVE ORDERS DISABLED',self.telegram.sent[-1][1])
    def test_cannot_issue_live_trade_command(self):
        self.bot.handle(self.user('/buy AAPL 100'))
        self.assertIn('Unknown command',self.telegram.sent[-1][1])
        self.assertFalse(any('/live/orders' in p for _,p,_ in self.talos.calls))

if __name__=='__main__':unittest.main()
