"""Reviewed example trusted local Python strategy, intentionally trivial.
Returns daily binary exposure after an N-day SMA; orders execute next session.
"""
def signals(bars,params):
    lookback=int(params.get('lookback',20))
    if not 3<=lookback<=300:raise ValueError('lookback outside approved range')
    out=[];total=0.0
    for i,b in enumerate(bars):
        total+=b['close']
        if i>=lookback:total-=bars[i-lookback]['close']
        out.append(int(i>=lookback-1 and b['close']>total/lookback))
    return out
