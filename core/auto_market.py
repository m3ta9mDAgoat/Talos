"""Bounded Yahoo Finance daily chart reader for research-only OHLCV.

No hidden synthetic-data fallback, user-supplied URL, live quote or brokerage feed.
Data vendor may block, rate-limit, revise or withdraw responses without warning.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .market_data import normalize

MAX_RESPONSE_BYTES = 4_000_000
MAX_BARS = 10_000
SYMBOL = re.compile(r'^[A-Z0-9^][A-Z0-9.^=_-]{0,29}$')

class MarketProviderError(RuntimeError):
    pass


def yahoo_chart_daily(symbol: str, start: date, end: date, *, opener=None):
    """Get daily Yahoo chart quote OHLCV, preserving dividend/split observations.

    Quotes are presented by Yahoo in split-adjusted historical units; dividend
    cashflows are NOT added by the Talos reference execution model. Returns a
    list of ascending normalized bars; provenance belongs to the caller.
    """
    if not SYMBOL.fullmatch(symbol) or '/' in symbol:
        raise ValueError('Invalid Yahoo symbol. Use AAPL, MSFT, SPY or BTC-USD.')
    if end <= start or end > date.today() or (end-start).days > 365*15:
        raise ValueError('Date range must end in the past/today and cover at most 15 years.')
    if start < date(1990, 1, 1):
        raise ValueError('Yahoo import supports dates from 1990 onward.')
    start_utc = int(datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    end_utc = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + symbol + '?' + urlencode({
        'period1':start_utc,'period2':end_utc,'interval':'1d','events':'div,splits',
        'includeAdjustedClose':'true'})
    req = Request(url, headers={'User-Agent':'Mozilla/5.0 (compatible; TalosResearch/2.6; historical daily OHLCV)',
                                'Accept':'application/json'})
    try:
        response = (opener or urlopen)(req, timeout=15)
        with response as res:
            payload = res.read(MAX_RESPONSE_BYTES + 1)
            if len(payload)>MAX_RESPONSE_BYTES:
                raise MarketProviderError('Yahoo response exceeded 4 MB limit')
    except HTTPError as e:
        if e.code in (401, 403, 429):
            raise MarketProviderError(f'Yahoo denied market-data request (HTTP {e.code}); try later or another provider.') from e
        raise MarketProviderError(f'Yahoo market-data HTTP {e.code}') from e
    except (URLError, TimeoutError, OSError) as e:
        raise MarketProviderError('Yahoo historical data unavailable; check provider access or retry later.') from e
    try:
        document=json.loads(payload)
        chart=document['chart']
        if chart.get('error'):
            raise MarketProviderError('Yahoo returned a provider error: '+str(chart['error'].get('description','unavailable'))[:130])
        results=chart.get('result') or []
        if not results:
            raise MarketProviderError('Yahoo returned no data for this ticker or date range.')
        series=results[0]
        timestamps=series.get('timestamp') or []
        quote=(series.get('indicators',{}).get('quote') or [])[0]
        required=('open','high','low','close','volume')
        if any(len(quote.get(k) or []) != len(timestamps) for k in required):
            raise MarketProviderError('Yahoo data columns are incomplete.')
        dividends={datetime.fromtimestamp(int(k),tz=timezone.utc).date().isoformat(): float(v.get('amount',0))
                   for k,v in (series.get('events',{}).get('dividends') or {}).items()}
        splits={}
        for key,event in (series.get('events',{}).get('splits') or {}).items():
            numerator=event.get('numerator'); denominator=event.get('denominator')
            if denominator and numerator:
                splits[datetime.fromtimestamp(int(key),tz=timezone.utc).date().isoformat()]=float(numerator)/float(denominator)
        bars=[]
        for i,ts in enumerate(timestamps):
            stamp=datetime.fromtimestamp(ts,tz=timezone.utc).date().isoformat()
            if not start.isoformat()<=stamp<=end.isoformat():continue
            if any(quote[k][i] is None for k in required[:4]):continue
            row={'date':stamp,**{k:float(quote[k][i]) for k in required[:4]},
                 'volume':float(quote['volume'][i] or 0),'dividend':dividends.get(stamp,0),
                 'split':splits.get(stamp,0)}
            # Reject corrupt market bars instead of inventing synthetic replacement.
            bars.append(row)
        if len(bars)>MAX_BARS:raise MarketProviderError('Too many daily bars returned')
        normalized=normalize(bars,allow_weekends=False if not symbol.endswith(('-USD','-USDT')) else True)
        if len(normalized)<10:raise MarketProviderError('Fewer than ten valid historical bars returned by Yahoo.')
        return normalized
    except MarketProviderError:
        raise
    except (KeyError,IndexError,TypeError,ValueError,OverflowError,AttributeError) as e:
        raise MarketProviderError('Yahoo market-data response was invalid or inconsistent.') from e
