"""Opt-in 15-min read-only bridge from Talos v4.7.8 -> Research Plane.

Needs two service secrets, locally set by operator; NEVER upload/send them to chat.
No legacy mutations, no Bot API, no trading orders, no replay alerts.
"""
from __future__ import annotations
import argparse,os,time,urllib.parse,json,sys
import httpx


def config():
    legacy=os.getenv('TALOS_LEGACY_BASE_URL','').rstrip('/')
    lab=os.getenv('TALOS_LAB_BASE_URL','').rstrip('/')
    key=os.getenv('TALOS_RESEARCH_BRIDGE_SHARED_SECRET','')
    admin=os.getenv('TALOS_LAB_ADMIN_TOKEN','')
    for uri in (legacy,lab):
        p=urllib.parse.urlparse(uri)
        if not (p.scheme=='https' or (p.scheme=='http' and p.hostname in ('127.0.0.1','localhost'))):
            raise RuntimeError('Only HTTPS and loopback HTTP are allowed')
        if p.username or p.password or p.query:raise RuntimeError('No credentials or query in base URL')
    if len(key)<32 or len(admin)<24:raise RuntimeError('Missing strong bridge key or Research Admin token')
    return legacy,lab,key,admin


def sync_once(client:httpx.Client,legacy:str,lab:str,key:str,admin:str,budget=10):
    headers={'Authorization':'Bearer '+admin}
    status=client.get(lab+'/api/v2/research/telegram/legacy/status',headers=headers)
    status.raise_for_status();cursor=int(status.json()['cursor'])
    imported=0;pages=0;last_archive=None
    for _ in range(budget):
        result=client.get(legacy+'/api/v1/settings/research-bridge/telegram/events',
                          headers={'X-Talos-Bridge-Key':key},params={'after_id':cursor,'limit':150})
        result.raise_for_status();doc=result.json()
        if doc.get('schema')!='talos.legacy_telegram.page.v1' or doc.get('read_only') is not True:
            raise ValueError('Unexpected response from legacy bridge')
        next_cursor=int(doc['next_after_id'])
        if next_cursor<cursor or (doc.get('has_more') and next_cursor<=cursor):
            raise ValueError('Legacy source cursor did not progress')
        rows=doc.get('events',[])
        if rows:
            r=client.post(lab+'/api/v2/research/telegram/legacy/batch',headers=headers,
                          json={'schema_version':'talos.legacy_telegram.page.v1','events':rows})
            r.raise_for_status();out=r.json()
            imported+=int(out['inserted']);last_archive=out.get('archive_id')
        pages+=1;cursor=next_cursor
        if not doc.get('has_more'):break
    return {'new_observations':imported,'pages':pages,'last_legacy_id':cursor,
            'research_archive_id':last_archive,'notifications_sent':False,
            'old_telegram_database_modified':False}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--watch',action='store_true',help='Continue syncing no more than every 15 minutes')
    p.add_argument('--interval-seconds',type=int,default=900)
    a=p.parse_args()
    if a.interval_seconds<900:raise SystemExit('Minimal sync interval is 900 seconds to protect Render/Neon quotas')
    legacy,lab,key,admin=config()
    with httpx.Client(timeout=35,follow_redirects=False) as client:
        while True:
            try:print(json.dumps(sync_once(client,legacy,lab,key,admin),ensure_ascii=False),flush=True)
            except Exception as e:
                # Don't print request URLs, headers, env contents or secrets.
                print('Bridge cycle failed: '+type(e).__name__,file=sys.stderr,flush=True)
                if not a.watch:raise SystemExit(1)
            if not a.watch:break
            time.sleep(a.interval_seconds)
if __name__=='__main__':main()
