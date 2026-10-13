"""Windows/local compute agent: remote queue -> local research engine -> result upload.
No secrets or user Python code in task payloads; engine execution allowlisted.
"""
from __future__ import annotations
import argparse,os,threading,sys,time,secrets,platform
from urllib.parse import urlparse
from pathlib import Path
import httpx
from .execute import execute

class Agent:
    def __init__(self,url=None,token=None,owner=None,poll=8,transport=None):
        self.url=(url or os.getenv('TALOS_LAB_URL','http://127.0.0.1:8018')).rstrip('/')
        host=urlparse(self.url)
        if host.scheme!='https' and host.hostname not in ('127.0.0.1','localhost','::1'):
            raise ValueError('Remote Worker API requires HTTPS')
        self.token=token or os.getenv('TALOS_LAB_WORKER_TOKEN')
        if not self.token or len(self.token)<24:raise ValueError('Missing long worker bearer secret')
        self.owner=owner or 'worker_'+secrets.token_hex(8)
        self.client=httpx.Client(timeout=45,transport=transport,headers={'authorization':'Bearer '+self.token,'x-worker-id':self.owner,**({'x-worker-capabilities':'hosted_reference'} if os.getenv('TALOS_LAB_WORKER_MODE')=='hosted_reference' else {})})
        self.poll=float(os.getenv('TALOS_LAB_POLL_SECONDS',str(poll)))
    def get_dataset(self,id):
        r=self.client.get(self.url+'/api/v2/worker/datasets/'+id);r.raise_for_status();return r.json()
    def get_archive(self,id):
        r=self.client.get(self.url+'/api/v2/worker/telegram/'+id);r.raise_for_status();return r.json()['events']
    def once(self):
        r=self.client.post(self.url+'/api/v2/worker/claim');r.raise_for_status()
        task=r.json().get('job')
        if not task:return False
        jid=task['id'];stop=threading.Event();lost=threading.Event()
        def renew():
            while not stop.wait(max(2,int(task['lease_seconds'])//3)):
                try:
                    h=self.client.post(self.url+'/api/v2/worker/jobs/'+jid+'/heartbeat',json={'owner':self.owner})
                    if h.status_code!=200:lost.set();return
                except Exception:lost.set();return
        thread=threading.Thread(target=renew,daemon=True);thread.start()
        result=None;error=None
        try:result=execute(task,dataset_fetch=self.get_dataset,archive_fetch=self.get_archive)
        except Exception as e:error=type(e).__name__+': '+str(e)[:500]
        finally:stop.set();thread.join(timeout=3)
        if lost.is_set():
            print('Lost job lease; refusing to upload stale output',jid,flush=True)
            return True
        resp=self.client.post(self.url+'/api/v2/worker/jobs/'+jid+'/finish',
                              json={'owner':self.owner,'result':result,'error':error},timeout=50)
        if resp.status_code!=200:print('Job completion rejected',jid,resp.status_code,flush=True)
        else:print('Job finished:',jid,'COMPLETE' if not error else 'FAILED',flush=True)
        return True
    def announce(self):
        mode=os.getenv('TALOS_LAB_WORKER_MODE','windows_remote')
        harness=(platform.system()=='Windows' and (Path(os.getenv('TALOS_STAGE3_ROOT','C:/TalosEngines/TalosStage3'))/'windows'/'RUN_STAGE3.ps1').is_file())
        payload={'worker_id':self.owner,'platform':platform.system(),'mode':mode,
                 'stage3_harness_detected':bool(harness)}
        try:
            self.client.post(self.url+'/api/v2/integration/worker/heartbeat',json=payload,timeout=12).raise_for_status()
        except (httpx.HTTPError,ValueError) as exc:
            print('Component heartbeat failed:',type(exc).__name__,flush=True)

    def serve(self):
        print('Talos research agent:',self.owner,'server:',self.url,'LIVE ORDERS DISABLED',flush=True)
        idle=0
        last_announce=0.0
        while True:
            if time.monotonic()-last_announce>=60:
                self.announce()
                last_announce=time.monotonic()
            try:
                if self.once():idle=0
                else:
                    idle+=1
                    # Local UI responsiveness, without noisy remote Render/Neon polling.
                    # Remote capped at 90s to preserve free-tier resources.
                    local=urlparse(self.url).hostname in ('127.0.0.1','localhost','::1')
                    base=5.0 if local else max(30.0,self.poll)
                    ceiling=20.0 if local else 90.0
                    time.sleep(min(ceiling,base*(2**min(idle-1,3))))
            except (httpx.HTTPError,ValueError) as exc:
                print('Worker transient/error:',type(exc).__name__,str(exc)[:140],flush=True)
                time.sleep(min(60,self.poll*3))

if __name__=='__main__':
    Agent(poll=60).serve()
