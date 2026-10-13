"""Safe stdlib-only Telegram polling daemon. Requires a NEW BotFather bot."""
from __future__ import annotations
import json,os,sys,time,traceback
from pathlib import Path
from .api import TalosClient,TelegramClient,APIError
from .db import BotDB
from .service import BotService

ROOT=Path(__file__).resolve().parents[1]

def load_config(path):
    cfg=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(cfg.get('owner_user_ids'),list) or not isinstance(cfg.get('source_chat_ids'),list):
        raise ValueError('owner_user_ids/source_chat_ids must be JSON arrays')
    for x in cfg['owner_user_ids']+cfg['source_chat_ids']:
        if isinstance(x,bool) or not isinstance(x,int):raise ValueError('IDs must be integer values, not strings')
    cfg['poll_seconds']=max(5,min(30,int(cfg.get('poll_seconds',20))))
    cfg['auto_archive_seconds']=max(60,min(3600,int(cfg.get('auto_archive_seconds',120))))
    if not str(cfg.get('talos_url','')).startswith(('http://127.0.0.1:','http://localhost:','https://')):
        raise ValueError('Research API must use HTTPS unless bound to localhost')
    return cfg

def run():
    path=Path(os.getenv('TALOS_BOT_CONFIG',str(ROOT/'bot_config.json')))
    if not path.is_file():
        print('Missing bot_config.json. Copy config.example.json and set NEW BotFather token and owner ID locally.');return 2
    cfg=load_config(path)
    if os.getenv('TALOS_BOT_TALOS_URL'):
        cfg['talos_url']=os.environ['TALOS_BOT_TALOS_URL']
    if os.getenv('TALOS_BOT_RESEARCH_ROOT'):
        cfg['research_root']=os.environ['TALOS_BOT_RESEARCH_ROOT']
    if not str(cfg.get('talos_url','')).startswith(('http://127.0.0.1:','http://localhost:','https://')):
        raise ValueError('Research API must use HTTPS unless localhost')
    tg=TelegramClient(cfg['bot_token'])
    info=tg.request('getWebhookInfo')
    if info.get('url'):
        print('This bot has an active Telegram webhook; use a NEW BotFather token or disable its webhook manually. Existing bot is untouched.');return 3
    myself=tg.request('getMe')
    db=BotDB(Path(os.getenv('TALOS_BOT_DB',str(path.parent/'.bot_runtime.sqlite3'))))
    talos=TalosClient(cfg['talos_url'],cfg['research_root'])
    svc=BotService(cfg,db,talos,tg)
    print('Talos Telegram BOT v2.1 @'+str(myself.get('username','unknown')))
    print('Owner IDs configured:',len(svc.owner_ids),'| Authorized source chats:',len(svc.source_ids))
    if not svc.owner_ids: print('SAFE SETUP: only /id is available. Configure owner_user_ids before using private commands.')
    print('Research API:',cfg['talos_url'],'| LIVE ORDERS DISABLED')
    def beat():
        try:
            talos.post('/api/v2/integration/bot/heartbeat',{'bot_name':str(myself.get('username') or 'talos_bot')[:64],
                'captured_events':db.count(),'archive_id':db.meta('archive_id','') or None})
        except (APIError,OSError,ValueError):pass
    beat()
    last_sync=time.monotonic();last_jobs=time.monotonic();last_beat=time.monotonic()
    while True:
        try:
            for upd in tg.updates(db.offset(),cfg['poll_seconds']):
                uid=upd['update_id']
                if uid<db.offset():continue
                try:svc.handle(upd)
                except Exception as exc:
                    # Do not log Telegram token, credentials or raw message contents.
                    print('Update error',uid,type(exc).__name__)
                    time.sleep(1)
                    break
                db.save_update(uid)
            tick=time.monotonic()
            if tick-last_beat>=60:
                beat();last_beat=tick
            if tick-last_jobs>=20:
                try:svc.notify_tracked()
                except Exception as ex:print('Job watch error:',type(ex).__name__)
                last_jobs=tick
            if tick-last_sync>=cfg['auto_archive_seconds'] and db.meta('archive_dirty')=='1':
                try:svc.sync_archive()
                except (APIError,ValueError) as ex: print('Archive sync pending:',type(ex).__name__)
                last_sync=tick
        except KeyboardInterrupt:
            print('Telegram bot stopped. Old bot and Talos remain untouched.');return 0
        except (APIError,OSError) as ex:
            print('Telegram polling temporarily unavailable:',type(ex).__name__)
            time.sleep(5)

def main():
    try:return run()
    except (ValueError,FileNotFoundError,json.JSONDecodeError) as exc:
        print('Configuration error:',str(exc).replace(os.environ.get('TALOS_LAB_ADMIN_TOKEN','__never__'),'[redacted]'));return 2

if __name__=='__main__':sys.exit(main())
