"""Telegram Bot v2.1: owner-only research commands + audited channel capture.
No trading orders, no AI-generated trading advice, no execution path to brokers.
"""
from __future__ import annotations
import base64,hashlib,json,re
from datetime import datetime,timezone
from .api import APIError

HEX64=re.compile(r'^[0-9a-f]{64}$')
SYMBOL=re.compile(r'^[A-Z0-9][A-Z0-9_./:=^-]{0,30}$')
PLUGINS={'ema_cross','sma_cross','rsi_reversion','buy_hold'}
TERMINAL={'complete','failed','cancelled'}

def stamp():return datetime.now(timezone.utc).isoformat()

def shrink(s,n=120):return str(s)[:n]

class BotService:
    def __init__(self,config,db,talos,telegram):
        self.cfg=config;self.db=db;self.talos=talos;self.tg=telegram
        self.owner_ids={int(x) for x in config['owner_user_ids']}
        self.source_ids={int(x) for x in config['source_chat_ids']}
    def _say(self,chat,text):
        if not self.tg: return
        self.tg.send(chat,text)
    def ingest_channel(self,update):
        event_type=None;data=None
        if 'channel_post' in update:
            data=update['channel_post'];event_type='new'
        elif 'edited_channel_post' in update:
            data=update['edited_channel_post'];event_type='edit'
        if not data:return False
        chat=data.get('chat') or {};chat_id=chat.get('id')
        if not self.source_ids or chat_id not in self.source_ids:return False
        mid=data.get('message_id');text=str(data.get('text') or data.get('caption') or '')
        if mid is None or not text:return False
        # Ingest time, NOT Telegram's original publication time, is the conservative
        # first-seen boundary for future-safe quantitative replay.
        e={'chat_id':str(chat_id),'message_id':str(mid),'revision':str(update['update_id']),
           'timestamp':stamp(),'event_type':event_type,'text':text[:12000],
           'claimed_published_at':data.get('date'),
           'source_label':str(chat.get('title') or chat.get('username') or chat_id)[:120],
           'source_authenticated_by_bot':True}
        ok=self.db.ingest(update['update_id'],str(chat_id),e)
        if ok:self.db.set('archive_dirty','1')
        return ok
    def _import_forward(self,update,message,owner):
        text=str(message.get('text') or message.get('caption') or '')
        if not text:return False
        origin=message.get('forward_origin') or {}
        # Telegram-forwarded messages are user-selected observations, not an
        # authenticated full history. Save provenance as an explicit source label.
        original=origin.get('chat') or origin.get('sender_chat') or {}
        source_name=str(original.get('title') or original.get('username') or 'forwarded_user_source')
        chat='forwarded:'+str(original.get('id') or owner)
        e={'chat_id':chat,'message_id':str(message.get('message_id')),
           'revision':str(update['update_id']),'timestamp':stamp(),'event_type':'new',
           'text':text[:12000],'source_label':source_name[:120],
           'source_authenticated_by_bot':False}
        ok=self.db.ingest(update['update_id'],chat,e)
        if ok:self.db.set('archive_dirty','1')
        return ok
    def handle(self,update):
        # No listener on private messages beyond allowed owner IDs.
        if self.ingest_channel(update):return
        message=update.get('message') or update.get('edited_message')
        if not message:return
        chat=message.get('chat') or {}
        if chat.get('type')!='private':return
        sender=(message.get('from') or {}).get('id')
        if sender is None:return
        text=str(message.get('text') or '').strip()
        parts=text.split(maxsplit=5) if text else []
        cmd=(parts[0].split('@')[0].lower() if parts and parts[0].startswith('/') else '')
        if cmd in ('/id','/whoami'):
            self._say(chat['id'],f'Telegram user ID: {sender}\nAdd this numeric ID to owner_user_ids in bot_config.json on your own computer.')
            return
        if int(sender) not in self.owner_ids:return
        try:
            self.current_update_id=int(update.get('update_id',0))
            if message.get('document') and not cmd:
                self.import_document(chat['id'],message['document']);return
            if not cmd:
                if message.get('forward_origin') or message.get('forward_from_chat'):
                    self._import_forward(update,message,sender)
                    self._say(chat['id'],'Forwarded message recorded locally. Use /sync then /replay or /verify. Authorship/time not independently certified.')
                return
            self.command(chat['id'],sender,cmd,parts[1:])
        except (APIError,ValueError,KeyError,TypeError) as exc:
            self._say(chat['id'],'Talos: '+shrink(exc,250))
    def sync_archive(self):
        events=self.db.archive_payload()
        if not events:raise ValueError('No Telegram observations captured. Authorize a channel or forward a message first.')
        out=self.talos.post('/api/v2/research/telegram/import',{'source':'talos_new_bot_observations','events':events})
        self.db.set('archive_id',out['id']);self.db.set('archive_dirty','0');self.db.set('last_sync_at',stamp())
        return out['id'],len(events)
    def archive_id(self):
        if self.db.meta('archive_dirty','0')=='1' or not self.db.meta('archive_id',''):
            id,_=self.sync_archive();return id
        return self.db.meta('archive_id')
    def _job(self,chat,owner,body):
        update_id=getattr(self,'current_update_id',0)
        if update_id>0:
            body['idempotency_key']='talosbot_'+str(update_id)
        out=self.talos.post('/api/v2/research/jobs',body)
        self.db.track_job(out['id'],owner,out.get('status','queued'))
        self._say(chat,f"Research job: {out['id']}\nStatus: {out.get('status','queued')}\nNo live orders. /job {out['id']}")
    def _validate_dataset(self,s):
        if not HEX64.fullmatch(s):raise ValueError('dataset_id must be the 64-character SHA256 returned by Talos Data Library')
        return s
    def _dataset(self,symbol,ds):
        return {'source':'dataset','dataset_id':ds,'symbol':symbol,'interval':'1d','start':'2020-01-01','end':'2023-12-31'}
    def command(self,chat,owner,cmd,args):
        if cmd in ('/start','/help'):
            return self._say(chat,'TALOS BOT v2.1 (research only)\n'
                '/status /strategies /jobs /job ID /cancel ID\n'
                '/backtest ema_cross AAPL DATASET_SHA256\n'
                '/tgtest AAPL DATASET_SHA256\n'
                '/sync /replay /verify AAPL DATASET_SHA256 [sessions]\n'
                '/sources /risk /paper /missed /id\n'
                'Forward a Telegram post or Phanes XLSX (max 1.5MB).\n'
                'Enable channels only by putting their numeric IDs in source_chat_ids.\n'
                'Backtests are reference-only; LIVE ORDERS DISABLED.')
        if cmd=='/status':
            h=self.talos.get('/health');latest=self.db.meta('archive_id','')
            return self._say(chat,f"Talos {h.get('version')} API: {h.get('status')}\nWorker jobs: /jobs\n"
                f"Captured updates: {self.db.count()}\nArchive: {latest[:16] if latest else 'none'}\n"
                'Live orders: DISABLED')
        if cmd=='/strategies':
            c=self.talos.get('/api/v2/research/catalog')
            return self._say(chat,'Reference research strategies:\n'+', '.join(x['id'] for x in c['plugins'])+
                '\nTelegram recommendation strategy: telegram_signal (needs archive and OHLCV).\n'
                'Native LEAN/Nautilus: Stage3 fixture only; not generalized.')
        if cmd=='/jobs':
            jobs=self.talos.get('/api/v2/research/jobs')
            return self._say(chat,'Recent jobs:\n'+('\n'.join(f"{x['id'][:8]}  {x['status']}  {x['kind']}" for x in jobs[:8]) if jobs else 'No jobs'))
        if cmd=='/job':
            if len(args)!=1:raise ValueError('Usage: /job FULL_JOB_UUID')
            x=self.talos.get('/api/v2/research/jobs/'+args[0])
            result=x.get('result') or {};metric=(result.get('backtest') or {}).get('metric') or {}
            lines=[f"Job: {x['id']}",f"Status: {x['status']}"]
            for key in ('end_equity','total_return_pct','max_drawdown_pct','sharpe'):
                if key in metric:lines.append(f'{key}: {metric[key]}')
            if x.get('error'):lines.append('Error: '+shrink(x['error'],130))
            if result.get('telegram_event_audit'):lines.append('Telegram audit: '+shrink(result['telegram_event_audit'],210))
            return self._say(chat,'\n'.join(lines))
        if cmd=='/cancel':
            if len(args)!=1:raise ValueError('Usage: /cancel FULL_JOB_UUID')
            x=self.talos.post('/api/v2/research/jobs/'+args[0]+'/cancel',{})
            return self._say(chat,'Job '+shrink(args[0],40)+': '+str(x.get('status')))
        if cmd=='/backtest':
            if len(args)!=3:raise ValueError('Usage: /backtest ema_cross AAPL DATASET_SHA256')
            plugin,sym,ds=args[0].lower(),args[1].upper(),self._validate_dataset(args[2])
            if plugin not in PLUGINS:raise ValueError('Allowed: '+', '.join(sorted(PLUGINS)))
            if not SYMBOL.fullmatch(sym):raise ValueError('Invalid symbol')
            return self._job(chat,owner,{'kind':'research','request':{'engine':'reference','data':self._dataset(sym,ds),
                'strategy':{'plugin':plugin},'walk_forward':True,'monte_carlo':200,'robustness':False}})
        if cmd=='/tgtest':
            if len(args)!=2:raise ValueError('Usage: /tgtest AAPL DATASET_SHA256')
            sym,ds=args[0].upper(),self._validate_dataset(args[1])
            if not SYMBOL.fullmatch(sym):raise ValueError('Invalid symbol')
            arch=self.archive_id()
            return self._job(chat,owner,{'kind':'research','request':{'engine':'reference','data':self._dataset(sym,ds),
                'strategy':{'plugin':'telegram_signal','parameters':{'archive_id':arch}},
                'walk_forward':False,'monte_carlo':0}})
        if cmd in ('/sync','/archive'):
            arch,count=self.sync_archive()
            return self._say(chat,f'Immutable archive synced: {count} observations\nArchive ID: {arch}\nSource times: first observed by this bot.')
        if cmd=='/replay':
            arch=self.archive_id()
            return self._job(chat,owner,{'kind':'telegram_replay','request':{'engine':'reference',
                'strategy':{'plugin':'telegram_signal','parameters':{'archive_id':arch}},
                'walk_forward':False,'monte_carlo':0}})
        if cmd=='/verify':
            if len(args) not in (2,3):raise ValueError('Usage: /verify AAPL DATASET_SHA256 [horizon_sessions]')
            sym=args[0].upper();ds=self._validate_dataset(args[1]);h=int(args[2]) if len(args)==3 else 5
            if not SYMBOL.fullmatch(sym) or not 1<=h<=120:raise ValueError('Invalid symbol or horizon')
            observed=self.db.last_events(500)
            if not observed:raise ValueError('No observed recommendations')
            records=[{'post_id':str(e['chat_id'])+':'+str(e['message_id']),
                      'author':str(e.get('source_label') or e['chat_id']),
                      'first_observed_at':e['timestamp'],'event_type':e['event_type'],
                      'text':e['text']} for e in observed]
            output=self.talos.post('/api/v2/replay/verify',{'source_kind':'telegram','source_label':'Talos bot source observations',
                'symbol':sym,'dataset_id':ds,'horizon_sessions':h,'events':records})
            summary=output['summary']
            saved=self.db.record_verification(output.get('recommendations',[]),sym,h)
            return self._say(chat,f"Replay score for {sym} (price-only, not verified fills):\n"
                f"Scored: {summary['recommendations_scored']} | Wins: {summary['wins']} | Losses: {summary['losses']}\n"
                f"Accuracy of scored: {summary.get('accuracy_pct_of_scored')}%\n"
                f'Saved {saved} new scored observations for /missed. No lookahead: first observed → next daily session open. No notifications generated.')
        if cmd=='/sources':
            return self._say(chat,f"Authorized source chat IDs: {len(self.source_ids)}\n"
                f"Stored events: {self.db.count()}\nBot API cannot detect arbitrary deleted channel posts.\n"
                'Historical posts cannot be retroactively authenticated by a newly added bot.')
        if cmd in ('/paper','/risk'):
            a=self.talos.get('/api/v2/research/paper')
            return self._say(chat,'Research paper simulation only (not a broker).\n'+
                shrink(a.get('account'),950)+'\nLive orders: DISABLED; risk gates differ across strategies.')
        if cmd=='/missed':
            data=self.db.outcome_summary(12)
            counts=data['counts']
            lines=[f"Verified recommendation scores (price-only): WIN {counts.get('WIN',0)} / LOSS {counts.get('LOSS',0)} / FLAT {counts.get('FLAT',0)}"]
            lines.extend(f"{r['symbol']} {r['outcome']} {r['return_pct']:.2f}% · {shrink(r['author'],24)}" for r in data['records'])
            lines.append('Manual /verify only. Not missed-trade broker PnL; no auto alerts without a timestamped outcome feed.')
            return self._say(chat,'\n'.join(lines))
        return self._say(chat,'Unknown command. Send /help.')
    def import_document(self,chat,doc):
        filename=str(doc.get('file_name',''))
        if not filename.lower().endswith('.xlsx'):raise ValueError('Only Phanes .xlsx export supported')
        if doc.get('file_size',0)>1_500_000:raise ValueError('Phanes Excel must be <=1.5MB')
        data=self.tg.file_bytes(doc['file_id'])
        out=self.talos.post('/api/v2/replay/phanes/import',{'filename':filename,'xlsx_base64':base64.b64encode(data).decode('ascii')})
        return self._say(chat,f"Phanes import saved: {out['archive_id']}\n"
            f"Records: {out.get('new_records_count')}\nManual export only; ATH is not verified PnL.")
    def notify_tracked(self):
        if not self.cfg.get('notify_job_results',True):return
        for job_id,owner_id,last_status in self.db.pending_jobs():
            try:job=self.talos.get('/api/v2/research/jobs/'+job_id)
            except APIError:continue
            status=job.get('status','')
            if status in TERMINAL and self.db.transition_job(job_id,status):
                # At most once, even if bot restarts. An interrupted notification
                # can be missed, not duplicated.
                self._say(int(owner_id),f"Talos research job {job_id[:8]}: {status}\n/job {job_id}\nLive orders disabled")
            elif status=='running' and last_status!='running':
                self.db.transition_job(job_id,'running')
