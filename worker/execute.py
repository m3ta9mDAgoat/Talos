"""Local worker dispatch; never executes arbitrary payload shell or Python."""
from __future__ import annotations
from pathlib import Path
import hashlib,json,os,subprocess,sys,platform,time
from core.contracts import ResearchRequest
from core.market_data import MarketData
from core.backtest import backtest,walk_forward,monte_carlo
from core.robustness import inspect as inspect_robustness
from core.telegram import replay,signal_timeline


def execute(task, dataset_fetch=None, archive_fetch=None, cache_dir=None):
    kind=task['kind'];raw=task['request'];spec=ResearchRequest.model_validate(raw)
    if kind=='research':
        if spec.engine!='reference':raise ValueError('Research jobs execute reference only')
        timer=time.perf_counter_ns()
        sourced=MarketData(cache_dir).acquire(spec.data,dataset_fetch)
        acquired=time.perf_counter_ns()
        bars=sourced.pop('bars')
        if spec.strategy.plugin=='telegram_signal':
            if archive_fetch is None:raise ValueError('Archive resolver unavailable')
            archive=archive_fetch(str(spec.strategy.parameters['archive_id']))
            if archive is None:raise ValueError('Archive not found')
            signal_series,tg_audit=signal_timeline(archive,bars,spec.data.symbol)
            result=backtest(bars,spec.strategy,spec.risk,signal_override=signal_series)
            wf={'status':'disabled','reason':'Event-time telegram strategy; no parameter optimization'}
            mc={'status':'disabled','simulations':0}
        else:
            tg_audit=None
            if len(bars)*spec.monte_carlo>3_000_000:raise ValueError('Monte Carlo exceeds 3 million bar-simulation budget; reduce simulations or data interval')
            result=backtest(bars,spec.strategy,spec.risk)
            wf=walk_forward(bars,spec.strategy,spec.risk) if spec.walk_forward else {'status':'disabled'}
            mc=monte_carlo(bars,spec.strategy,spec.risk,spec.monte_carlo,spec.random_seed)
        computed=time.perf_counter_ns()
        robustness=inspect_robustness(bars,spec.strategy,spec.risk,
                         simulations=spec.robustness_simulations,seed=spec.random_seed) if spec.robustness else {'status':'disabled'}
        analysed=time.perf_counter_ns()
        # bound output size before DB upload; sample intelligently for long runs
        if len(result['equity'])>3000:
            stride=max(1,len(result['equity'])//3000)
            result['equity']=result['equity'][::stride]
            result['equity_downsampled']=True
        return {'schema':'talos.research.result.v1','status':'COMPLETED','provenance':sourced,
                'strategy':spec.strategy.model_dump(),'backtest':result,
                'walk_forward':wf,'monte_carlo':mc,'robustness':robustness,'telegram_event_audit':tg_audit,
                'performance':{'dataset_acquire_ms':round((acquired-timer)/1e6,3),
                   'base_research_ms':round((computed-acquired)/1e6,3),
                   'robustness_ms':round((analysed-computed)/1e6,3),
                   'total_worker_compute_ms':round((time.perf_counter_ns()-timer)/1e6,3)},
                'live_trading_enabled':False,
                'data_warning':'Synthetic example' if spec.data.source=='sample' else 'Provider data unverified; inspect corporate actions and execution assumptions'}
    if kind=='native_parity':
        # Exactly installed, allowlisted fixture; not generic arbitrary strategy compilation.
        if spec.engine not in ('lean_parity','nautilus_parity'):raise ValueError('Unsupported native job')
        if spec.data.source!='native_fixture' or spec.data.symbol not in ('AAPL','MSFT'):
            raise ValueError('Native reference harness only covers AAPL/MSFT')
        root=Path(os.getenv('TALOS_STAGE3_ROOT','C:/TalosEngines/TalosStage3'))
        if platform.system()!='Windows':raise RuntimeError('Native Stage3 runner is available only on installed Windows machine')
        script=root/'windows'/'RUN_STAGE3.ps1'
        report=root/'outputs'/'comparison.json'
        if not script.is_file():raise FileNotFoundError('Stage3 Windows harness not installed')
        process=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script)],
                               cwd=str(root),timeout=1800,capture_output=True,text=True,errors='replace')
        if process.returncode!=0:raise RuntimeError('Native Stage3 run failed; inspect local outputs/comparison.json')
        doc=json.loads(report.read_text(encoding='utf-8'))
        if doc.get('strict_parity_passed') is not True:raise RuntimeError('Native parity report did not pass')
        if doc.get('schema')!='talos.cross_engine.parity.v2':raise RuntimeError('Wrong native report schema')
        # research result contains compact summaries and reproducible on-disk path;
        # preserve original detailed per-fill evidence on Windows.
        assets=[]
        for x in doc['assets']:
            assets.append({k:x[k] for k in ('symbol','status','orders_lean','orders_nautilus','matched_orders','months_compared',
                                            'end_equity_lean_usd','end_equity_nautilus_usd','max_abs_monthly_equity_difference_usd')})
        return {'schema':'talos.research.native-parity.v1','status':'COMPLETED','strict_parity_passed':True,
                'native_executed_on_windows':True,'assets':assets,
                'report_sha256':hashlib.sha256(report.read_bytes()).hexdigest(),
                'fixture_only':True,'not_general_native_backtest':True,'live_trading_enabled':False}
    if kind=='telegram_replay':
        if not spec.strategy.parameters.get('archive_id'):raise ValueError('archive_id missing in parameters')
        if archive_fetch is None:raise ValueError('Archive resolver unavailable')
        events=archive_fetch(str(spec.strategy.parameters['archive_id']))
        if events is None:raise ValueError('Archive not found')
        return {'schema':'talos.research.telegram-replay.v1',**replay(events),'live_trading_enabled':False}
    raise ValueError('Unsupported job kind')
