"""Versioned declarative strategy plugins; no arbitrary remote Python execution.
Trusted Python plugins can be written locally by implementing `signals(bars,params)`
and registered by explicit content sha256 via the on-disk manifest (never from API input).
"""
import hashlib,importlib.util,json,math,os
from pathlib import Path
from .contracts import StrategySpec

def signals(bars,spec:StrategySpec):
    n=len(bars);closes=[b['close'] for b in bars];out=[0]*n
    kind=spec.plugin
    if kind=='buy_hold':return [1]*n
    if kind=='trusted_python':
        return trusted_python_signals(bars,spec.trusted_name,spec.parameters,root=os.getenv('TALOS_TRUSTED_PLUGINS_DIR',
            str(Path(__file__).resolve().parents[1]/'trusted_plugins')))
    if kind=='telegram_signal':
        # Requires separately verified imported historical point-in-time timeline.
        raise ValueError('Telegram signal strategy requires timestamped archive-event association, not a daily OHLC-only feed')
    if kind in ('sma_cross','ema_cross'):
        if kind=='ema_cross':
            a=2/(spec.short+1);b=2/(spec.long+1);short=long=closes[0]
            for i,c in enumerate(closes):
                short=a*c+(1-a)*short;long=b*c+(1-b)*long
                if i>=spec.long-1:out[i]=int(short>long)
        else:
            fast=slow=0.0
            for i,c in enumerate(closes):
                fast+=c;slow+=c
                if i>=spec.short:fast-=closes[i-spec.short]
                if i>=spec.long:slow-=closes[i-spec.long]
                if i>=spec.long-1:out[i]=int(fast/spec.short>slow/spec.long)
    elif kind=='rsi_reversion':
        period=int(spec.parameters.get('period',14));period=min(100,max(2,period));state=0
        for i in range(period,n):
            changes=[closes[j]-closes[j-1] for j in range(i-period+1,i+1)]
            gain=sum(max(v,0) for v in changes)/period;loss=sum(max(-v,0) for v in changes)/period
            rsi=100.0 if loss==0 else 100-100/(1+gain/loss)
            if rsi<spec.rsi_buy:state=1
            if rsi>spec.rsi_sell:state=0
            out[i]=state
    else:raise ValueError(f'Unsupported plugin {kind}')
    return out

def trusted_python_signals(bars,plugin_name,parameters,root='trusted_plugins'):
    """Locally installed, explicitly allowlisted scripts only. API cannot upload code.
    Scripts have full local privileges: admin must review and pin the digest.
    """
    root=Path(root).resolve();manifest=root/'allowlist.json'
    if not manifest.is_file():raise RuntimeError('No approved local Python plugin allowlist')
    allowed=json.loads(manifest.read_text(encoding='utf-8'))
    if plugin_name not in allowed:raise PermissionError('Python plugin not allowlisted')
    if not plugin_name.isidentifier():raise ValueError('Invalid Python plugin identifier')
    path=(root/(plugin_name+'.py')).resolve()
    if path.parent!=root:raise ValueError('Path escape')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=allowed[plugin_name]:raise PermissionError('Python plugin hash mismatch')
    info=importlib.util.spec_from_file_location('talos_local_'+plugin_name,str(path))
    module=importlib.util.module_from_spec(info);info.loader.exec_module(module)
    result=module.signals(bars,parameters)
    if len(result)!=len(bars) or any(x not in (0,1) for x in result):raise ValueError('Bad plugin output')
    return list(result)
