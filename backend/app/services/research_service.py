from __future__ import annotations

import json
import re
from typing import Any
import httpx
from app.services.security import get_secret


async def search_crossref(query:str, limit:int=10)->list[dict[str,Any]]:
    out=[]
    async with httpx.AsyncClient(timeout=25,follow_redirects=True,headers={"User-Agent":"Talos/3.0 research platform"}) as client:
        try:
            r=await client.get('https://api.crossref.org/works',params={'query.bibliographic':query,'rows':limit});r.raise_for_status()
            for x in r.json().get('message',{}).get('items',[]):
                title=(x.get('title') or ['Untitled'])[0];abstract=re.sub('<[^>]+>',' ',x.get('abstract') or '');authors=[' '.join(filter(None,[a.get('given'),a.get('family')])) for a in x.get('author') or []]
                out.append({'provider':'Crossref','title':title,'doi':x.get('DOI'),'url':x.get('URL'),'abstract':' '.join(abstract.split()),'authors':authors,'journal':(x.get('container-title') or [''])[0]})
        except Exception:
            pass
        try:
            r=await client.get('https://export.arxiv.org/api/query',params={'search_query':f'all:{query}','start':0,'max_results':limit});r.raise_for_status()
            import xml.etree.ElementTree as ET
            root=ET.fromstring(r.content);ns={'a':'http://www.w3.org/2005/Atom'}
            for e in root.findall('a:entry',ns):
                title=' '.join((e.findtext('a:title',default='',namespaces=ns) or '').split());abstract=' '.join((e.findtext('a:summary',default='',namespaces=ns) or '').split());url=e.findtext('a:id',default='',namespaces=ns);authors=[a.findtext('a:name',default='',namespaces=ns) for a in e.findall('a:author',ns)]
                out.append({'provider':'arXiv','title':title,'doi':None,'url':url,'abstract':abstract,'authors':authors,'journal':'arXiv'})
        except Exception:
            pass
    seen=set();dedup=[]
    for x in out:
        k=(x.get('doi') or x.get('title','')).lower()
        if not k or k in seen:continue
        seen.add(k);dedup.append(x)
        if len(dedup)>=limit:break
    return dedup


def heuristic_draft(text:str,name:str)->dict[str,Any]:
    low=text.lower(); kind='technical'; feature='rsi'; op='<'; threshold=30.0
    if 'moving average' in low or 'sma' in low: feature='sma_distance_pct'; op='>'; threshold=0.0
    if 'momentum' in low: feature='return_pct'; op='>'; threshold=1.0
    if any(x in low for x in ['news','sentiment','twitter','social']): kind='source_event'
    if kind=='source_event':
        return {'name':name,'kind':'source_event','version':'1.0','definition':{'source_id':None,'symbols':['BTC/USDT'],'keywords_any':[],'keywords_none':[],'side':'buy','take_profit_pct':5.0,'stop_loss_pct':2.5,'max_holding_bars':72},'optimization_space':{'take_profit_pct':[2,10,2],'stop_loss_pct':[1,5,1]}}
    return {'name':name,'kind':'technical','version':'1.0','definition':{'symbols':['BTC/USDT'],'timeframe':'1h','entry_logic':'all','conditions':[{'feature':feature,'period':14,'op':op,'value':threshold}],'take_profit_pct':5.0,'stop_loss_pct':2.5,'max_holding_bars':72},'optimization_space':{'take_profit_pct':[2,10,2],'stop_loss_pct':[1,5,1]}}


async def draft_strategy(text:str,name:str='Research strategy')->dict[str,Any]:
    ai=get_secret('ai')
    base=(ai.get('base_url') or '').rstrip('/'); model=ai.get('model'); key=ai.get('api_key')
    if not base or not model:
        out=heuristic_draft(text,name); out['draft_mode']='heuristic'; out['warning']='AI provider not configured. This is a heuristic draft and must be reviewed before backtesting.'; return out
    prompt=("You convert trading research into strict Talos no-code strategy JSON. Return JSON only. "
            "Supported kinds: technical or source_event. Technical supports symbols,timeframe,entry_logic all/any, "
            "conditions with feature close|sma|ema|rsi|return_pct|volume_ratio|sma_distance_pct, period, op < <= > >= ==, value; "
            "take_profit_pct,stop_loss_pct,max_holding_bars. source_event supports source_id null,symbols,keywords_any,keywords_none,side buy, "
            "take_profit_pct,stop_loss_pct,max_holding_bars. Include optimization_space ranges [start,stop,step]. "
            "Do not invent undefined rules; list assumptions in warnings. Research:\n"+text[:12000])
    headers={'Content-Type':'application/json'}
    if key: headers['Authorization']=f'Bearer {key}'
    async with httpx.AsyncClient(timeout=60) as client:
        r=await client.post(base+'/chat/completions',headers=headers,json={'model':model,'messages':[{'role':'system','content':'Return valid JSON only.'},{'role':'user','content':prompt}],'temperature':0.1})
        r.raise_for_status(); content=r.json()['choices'][0]['message']['content']
    content=content.strip().removeprefix('```json').removesuffix('```').strip()
    out=json.loads(content); out.setdefault('name',name); out['draft_mode']='ai'; return out
