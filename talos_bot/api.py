"""Authenticated client for the unmodified Talos Research v2 control API."""
from __future__ import annotations
import json,os
from pathlib import Path
from urllib import request,error,parse

class APIError(RuntimeError): pass

class JsonClient:
    def __init__(self,base_url,token='',timeout=15):
        url=str(base_url).rstrip('/')
        if not (url.startswith('https://') or url.startswith('http://127.0.0.1:') or url.startswith('http://localhost:')):
            raise ValueError('Remote Talos API requires HTTPS; plaintext HTTP is local-only')
        self.base_url=url
        self.token=token
        self.timeout=timeout

    def call(self,method,path,payload=None):
        if not path.startswith('/') or path.startswith('//'): raise ValueError('Invalid API path')
        raw=None if payload is None else json.dumps(payload,ensure_ascii=False,allow_nan=False).encode('utf-8')
        headers={'Accept':'application/json'}
        if raw is not None: headers['Content-Type']='application/json'
        if self.token: headers['Authorization']='Bearer '+self.token
        req=request.Request(self.base_url+path,data=raw,headers=headers,method=method)
        try:
            with request.urlopen(req,timeout=self.timeout) as response:
                return json.loads(response.read(3_000_000).decode('utf-8'))
        except error.HTTPError as exc:
            try: detail=json.loads(exc.read(2000)).get('detail','Request rejected')
            except Exception: detail='Request rejected'
            raise APIError(f'HTTP {exc.code}: {str(detail)[:170]}') from None
        except (error.URLError,TimeoutError) as exc:
            raise APIError('Talos Research API unreachable; check local server or configured HTTPS endpoint') from None

class TalosClient(JsonClient):
    def __init__(self,base_url,research_root):
        token=os.environ.get('TALOS_LAB_ADMIN_TOKEN','')
        if len(token)<24:
            file=Path(research_root)/'.local_auth.json'
            if file.is_file():
                token=json.loads(file.read_text(encoding='utf-8')).get('admin','')
        if len(token)<24: raise ValueError('Missing Talos admin credential: start Talos locally first; never paste secret into chat')
        super().__init__(base_url,token)
    def get(self,path):return self.call('GET',path)
    def post(self,path,data):return self.call('POST',path,data)

class TelegramClient(JsonClient):
    def __init__(self,token):
        if not token or ':' not in token or len(token)<30:raise ValueError('Invalid Telegram bot token. Create a NEW bot using @BotFather')
        super().__init__('https://api.telegram.org/bot'+token,token='',timeout=40)
        self.bot_token=token
    def call(self,method,path,payload=None):
        try:result=super().call(method,path,payload)
        except APIError as ex:
            # Never include the Bot API URL in exception messages, as URL contains token.
            raise APIError('Telegram request failed: '+str(ex).split('HTTP ')[-1][:160]) from None
        if not result.get('ok'):raise APIError('Telegram returned unsuccessful response')
        return result.get('result')
    def request(self,method,payload=None):return self.call('POST','/'+method,payload or {})
    def send(self,chat_id,text):
        msg=str(text)
        return self.request('sendMessage',{'chat_id':chat_id,'text':msg[:3900],'disable_web_page_preview':True})
    def updates(self,offset,timeout=20):
        # No deleteWebhook/drop_pending_updates: do not destroy data or change another bot's configuration.
        self.timeout=timeout+15
        return self.request('getUpdates',{'offset':offset,'timeout':timeout,'limit':50,
            'allowed_updates':['message','edited_message','channel_post','edited_channel_post']})
    def file_bytes(self,file_id,max_size=1_500_000):
        meta=self.request('getFile',{'file_id':file_id})
        if meta.get('file_size',0)>max_size:raise APIError('Excel exceeds 1.5 MB API limit')
        loc=meta.get('file_path','')
        if not loc or '..' in loc or loc.startswith('/') or not all(ch.isalnum() or ch in '/._-' for ch in loc):
            raise APIError('Unsafe Telegram file path')
        req=request.Request('https://api.telegram.org/file/bot'+self.bot_token+'/'+loc)
        try:
            with request.urlopen(req,timeout=30) as response:
                data=response.read(max_size+1)
        except (error.URLError,TimeoutError):raise APIError('Failed to download Telegram document') from None
        if len(data)>max_size:raise APIError('Excel exceeds 1.5 MB API limit')
        return data
