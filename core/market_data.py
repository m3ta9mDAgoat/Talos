"""Multi-provider daily OHLCV adapters and point-in-time provenance.
Network providers are optional, never use guessed values. CSV/dataset is fully offline.
"""
from __future__ import annotations
import csv,hashlib,json,math,os,time
from datetime import date,datetime,timezone
from pathlib import Path
from .contracts import DataSpec

BAR_KEYS=('date','open','high','low','close','volume')

def normalize(rows, allow_weekends=True):
    if not isinstance(rows,list) or len(rows)>40000:raise ValueError('Expected 1..40000 OHLCV rows')
    normalized=[];seen=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('OHLCV rows must be objects')
        stamp=str(row.get('date',''))[:10]
        date.fromisoformat(stamp)
        if stamp in seen or (normalized and stamp<=normalized[-1]['date']):raise ValueError(f'Duplicate/unsorted date {stamp}')
        seen.add(stamp)
        if not allow_weekends and date.fromisoformat(stamp).weekday()>4:raise ValueError('Weekend equity bar')
        try: numbers={k:float(row[k]) for k in BAR_KEYS[1:]}
        except (KeyError,TypeError,ValueError) as e:raise ValueError('Missing/invalid OHLCV field') from e
        if any(not math.isfinite(v) for v in numbers.values()):raise ValueError('Nonfinite price/volume')
        o,h,l,c,v=[numbers[k] for k in BAR_KEYS[1:]]
        if min(o,h,l,c)<=0 or v<0 or l>min(o,c) or h<max(o,c):raise ValueError(f'Invalid OHLCV {stamp}')
        dividend=float(row.get('dividend',0) or 0)
        split=float(row.get('split',0) or 0)
        if not math.isfinite(dividend) or dividend<0 or not math.isfinite(split) or split<0:raise ValueError('Invalid corporate action')
        normalized.append({'date':stamp,**numbers,'dividend':dividend,'split':split})
    if not normalized:raise ValueError('No bars available')
    return normalized

def csv_parse(text):
    src=csv.DictReader(text.splitlines())
    rows=[]
    for item in src:
        fixed={k.lower().strip().replace(' ','_'):v for k,v in item.items() if k}
        if 'timestamp' in fixed and 'date' not in fixed:fixed['date']=fixed['timestamp']
        if 'dividends' in fixed:fixed['dividend']=fixed['dividends']
        if 'stock_splits' in fixed:fixed['split']=fixed['stock_splits']
        rows.append(fixed)
    return normalize(rows)

def sample_bars(symbol='AAPL',start='2020-01-01',end='2023-12-31'):
    # Never advertised as real market observations; deterministic test fixture only.
    from datetime import timedelta
    d=date.fromisoformat(str(start));last=date.fromisoformat(str(end));rows=[];i=0
    while d<=last:
        if d.weekday()<5:
            price=100 + i*.022 + 12*math.sin(i/65 + (0.3 if symbol=='MSFT' else 0))
            rows.append({'date':d.isoformat(),'open':round(price-.2,5),'high':round(price+1.3,5),
                         'low':round(price-1.4,5),'close':round(price,5),'volume':100000,
                         'dividend':0,'split':0})
            i+=1
        d+=timedelta(days=1)
    return rows

class MarketData:
    def __init__(self,cache_dir=None):
        self.path=Path(cache_dir or os.getenv('TALOS_LAB_DATA_CACHE','./datasets/cache')).resolve()
        self.path.mkdir(parents=True,exist_ok=True)
    def _cache(self,key,ttl,fetch):
        path=self.path/(hashlib.sha256(key.encode()).hexdigest()+'.json')
        if path.is_file() and time.time()-path.stat().st_mtime<ttl:
            try:
                obj=json.loads(path.read_text(encoding='utf-8'))
                if hashlib.sha256(json.dumps(obj['bars'],sort_keys=True,separators=(',',':')).encode()).hexdigest()==obj['sha256']:
                    return obj
            except (ValueError,KeyError):pass
        bars=normalize(fetch());payload=json.dumps(bars,sort_keys=True,separators=(',',':'))
        obj={'source_key':key,'fetched_at':datetime.now(timezone.utc).isoformat(),
             'sha256':hashlib.sha256(payload.encode()).hexdigest(),'bars':bars}
        temporary=path.with_suffix('.'+os.urandom(5).hex()+'.tmp');temporary.write_text(json.dumps(obj),encoding='utf-8')
        temporary.replace(path)
        return obj
    def acquire(self,spec:DataSpec,dataset_fetch=None):
        if spec.source=='sample':
            bars=sample_bars(spec.symbol,spec.start,spec.end)
            return {'source':'SYNTHETIC_SAMPLE_NOT_MARKET_DATA','bars':bars,'verified':False}
        if spec.source=='dataset':
            if dataset_fetch is None:raise ValueError('Dataset provider is unavailable')
            doc=dataset_fetch(spec.dataset_id)
            if doc is None:raise ValueError('Dataset ID not found')
            bars=normalize(doc['bars'])
            bars=[b for b in bars if str(spec.start)<=b['date']<=str(spec.end)]
            if not bars:raise ValueError('Dataset date window empty')
            provenance=doc.get('provenance') or {'source':'USER_CSV_UNVERIFIED_MARKET_PROVENANCE','verified':False}
            return {**provenance,'bars':bars,'dataset_id':spec.dataset_id,'verified':False}
        if spec.source=='yahoo':
            # HTTPS Yahoo Chart endpoint; no optional yfinance dependency on Render.
            # Never substitute sample_bars when a market provider is unavailable.
            from .auto_market import yahoo_chart_daily
            obj=self._cache('yahoo/chart/v2/'+spec.symbol+'/'+str(spec.start)+'/'+str(spec.end)+'/'+spec.interval,
                            43200,lambda:yahoo_chart_daily(spec.symbol,spec.start,spec.end))
            return {'source':'YAHOO_CHART_HISTORICAL_DAILY_RESEARCH', 'price_basis':'YAHOO_SPLIT_ADJUSTED_QUOTE_OHLC',
                    'dividends_in_reference_pnl':False,'verified':False,
                    'bars':obj['bars'],'sha256':obj['sha256'],'fetched_at':obj['fetched_at']}
        if spec.source=='ccxt':
            if spec.interval not in ('1d','1h'):raise ValueError('Unsupported crypto timeframe')
            def fetch():
                try:import ccxt
                except ImportError as e:raise RuntimeError('Install worker optional dependency ccxt') from e
                ex=getattr(ccxt,spec.exchange)({'enableRateLimit':True,'timeout':20000})
                ms=86400000 if spec.interval=='1d' else 3600000
                since=int(datetime(spec.start.year,spec.start.month,spec.start.day,tzinfo=timezone.utc).timestamp()*1000)
                last=int(datetime(spec.end.year,spec.end.month,spec.end.day,tzinfo=timezone.utc).timestamp()*1000)
                rows=[];seen=set()
                while since<=last and len(rows)<39500:
                    chunk=ex.fetch_ohlcv(spec.symbol,spec.interval,since=since,limit=1000)
                    if not chunk:break
                    for item in chunk:
                        ts=int(item[0]);day=datetime.fromtimestamp(ts/1000,tz=timezone.utc).date().isoformat()
                        if ts>last:break
                        if ts in seen:continue
                        seen.add(ts)
                        rows.append({'date':day,'open':item[1],'high':item[2],'low':item[3],
                                     'close':item[4],'volume':item[5]})
                    new_since=int(chunk[-1][0])+ms
                    if new_since<=since:break
                    since=new_since
                return rows
            # Current contract uses daily dates; hourly rows would collide. Refuse
            # rather than silently coerce multiple hours into one day.
            if spec.interval!='1d':raise ValueError('1h ingestion requires timestamp schema; not implemented')
            obj=self._cache('ccxt/'+spec.exchange+'/'+spec.symbol+'/'+str(spec.start)+'/'+str(spec.end)+'/'+spec.interval,1800,fetch)
            return {'source':'CCXT_'+spec.exchange.upper(),'bars':obj['bars'],'sha256':obj['sha256'],
                    'fetched_at':obj['fetched_at'],'verified':False}
        raise ValueError('Unknown data source')
