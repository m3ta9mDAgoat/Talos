"""REST control plane; durable and entirely non-executing on hosted server.
Runs independently alongside legacy Talos; never imports its watchers or credentials.
"""
from __future__ import annotations
import os,re,base64,binascii,threading,logging
from contextlib import asynccontextmanager
from fastapi import FastAPI,Depends,HTTPException,Request,Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse,HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from core.contracts import JobSubmit
from core.security import authorize
from core.market_data import normalize
from core.telegram import normalize_archive
from core.replay_verify import VerificationRequest, verify
from core.native_replay_plan import NativePlanRequest, build_plan
from core.phanes_export import workbook_to_archive, MAX_XLSX_BYTES
from core.trend_sentiment import TrendSentimentRequest, evaluate
from core.sentiment_models import TextDocument, vader_batch
from .storage import Store,paper_get,paper_action
from pathlib import Path

class PhanesWorkbookUpload(BaseModel):
    filename: str = Field(min_length=5,max_length=180)
    xlsx_base64: str = Field(min_length=1,max_length=2_100_000)

class DatasetUpload(BaseModel):
    symbol:str=Field('CUSTOM',min_length=1,max_length=36)
    bars:list[dict]=Field(min_length=10,max_length=30000)

class LegacyObservationUpload(BaseModel):
    schema_version: str = 'talos.legacy_telegram.page.v1'
    events: list[dict] = Field(max_length=150)


class WorkerHeartbeat(BaseModel):
    worker_id:str=Field(min_length=8,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    platform:str=Field(min_length=2,max_length=32)
    mode:str=Field(min_length=2,max_length=32)
    stage3_harness_detected:bool=False

class BotHeartbeat(BaseModel):
    bot_name:str=Field(min_length=1,max_length=64)
    captured_events:int=Field(ge=0,le=100000000)
    archive_id:str|None=Field(default=None,max_length=64)

class ArchiveUpload(BaseModel):
    source:str=Field('telegram_legacy_export',min_length=1,max_length=100)
    events:list[dict]=Field(max_length=3000)

class ResultPost(BaseModel):
    owner:str=Field(min_length=8,max_length=64)
    result:dict|None=None
    error:str|None=Field(None,max_length=1000)

class Heartbeat(BaseModel):
    owner:str=Field(min_length=8,max_length=64)

class PaperOrder(BaseModel):
    client_order_id:str=Field(min_length=8,max_length=100,pattern=r'^[A-Za-z0-9._:-]+$')
    symbol:str=Field(min_length=1,max_length=30)
    side:str
    qty:int=Field(ge=1,le=1000000)
    price:float=Field(gt=0,le=100000000)


def create_app(store:Store|None=None):
    @asynccontextmanager
    async def lifespan(app):
        # Small reference-only compute worker for Render's single-container setup.
        # Native LEAN/NautilusTrader jobs remain reserved for the Windows worker.
        enabled=(os.getenv('TALOS_MODE')=='hosted' and
                 os.getenv('TALOS_HOSTED_REFERENCE_WORKER')=='1')
        if enabled:
            from worker.agent import Agent
            os.environ['TALOS_LAB_WORKER_MODE']='hosted_reference'
            port=os.getenv('PORT','8018')
            agent=Agent(url=f'http://127.0.0.1:{port}',poll=5)
            thread=threading.Thread(target=agent.serve,name='talos-hosted-reference-worker',daemon=True)
            thread.start()
            logging.getLogger(__name__).info('Hosted reference worker started: native engines remain Windows-only')
        yield
    app=FastAPI(title='Talos Research Control Plane',version='2.5.0',lifespan=lifespan)
    app.state.store=store or Store()
    # Same-origin UI. CORS deliberately NOT opened to arbitrary origins.
    allowed=[p.strip() for p in os.getenv('TALOS_LAB_CORS_ORIGINS','').split(',') if p.strip()]
    if allowed:app.add_middleware(CORSMiddleware,allow_origins=allowed,allow_methods=['GET','POST'],allow_headers=['authorization','content-type'])

    def admin(r:Request):return authorize(r,'admin')
    def worker(r:Request):return authorize(r,'worker')
    def db(r:Request):return r.app.state.store

    @app.get('/health')
    def health():return {'status':'ok','component':'talos_research_control_plane','version':'2.5.0',
                         'live_orders_enabled':False,'legacy_data_migration_required':True}

    @app.get('/api/v2/integration/status',dependencies=[Depends(admin)])
    def integration_status(store=Depends(db)):
        recent=store.list()
        bot_state=store.component_status('telegram_bot')
        return {'schema':'talos.unified.integration.v1','site_ui':'approved_phase2',
                'research_api':'available',
                'hosted_reference_worker_enabled':os.getenv('TALOS_MODE')=='hosted' and os.getenv('TALOS_HOSTED_REFERENCE_WORKER')=='1',
                'hosted_native_execution':False, 'queued_jobs':sum(j['status']=='queued' for j in recent),
                'running_jobs':sum(j['status']=='running' for j in recent),
                'bot':bot_state,
                'windows_worker':store.component_status('windows_worker'),
                'hosted_reference_worker':store.component_status('hosted_reference_worker'),
                'archives_count':len(store.list_archives()),
                'telegram_ingestion':'bot authorized channel posts and forward import only',
                'legacy_telegram':'requires read-only migration; no automatic deletion',
                'native_execution':'Stage3 SMA AAPL/MSFT fixture only',
                'data':'reference/synthetic and uploaded; provider coverage not certified',
                'live_orders_enabled':False}

    @app.post('/api/v2/integration/bot/heartbeat',dependencies=[Depends(admin)])
    def bot_heartbeat(payload:BotHeartbeat,store=Depends(db)):
        # Heartbeat only; never accept or store BotFather token, Telegram chat content or credentials.
        return store.update_component('telegram_bot',payload.model_dump())

    @app.post('/api/v2/integration/worker/heartbeat',dependencies=[Depends(worker)])
    def worker_component_heartbeat(payload:WorkerHeartbeat,store=Depends(db)):
        name=('hosted_reference_worker' if payload.mode=='hosted_reference' else 'windows_worker')
        if name=='windows_worker' and payload.platform!='Windows':
            raise HTTPException(422,'Non-Windows native agent cannot report Windows presence')
        return store.update_component(name,payload.model_dump())

    @app.get('/api/v2/research/evidence/stage3',dependencies=[Depends(admin)])
    def verified_stage3_evidence():
        # Archival user-supplied certificate only; hashes refer to logs not bundled.
        import json,hashlib
        source=Path(__file__).resolve().parents[1]/'artifacts'/'LEAN_NAUTILUS_Stage3_parity_evidence.json'
        raw=source.read_bytes()
        data=json.loads(raw)
        return {'source':'USER_SUPPLIED_STAGE3_COMPARISON_NOT_REEXECUTED',
                'sha256':hashlib.sha256(raw).hexdigest(),
                'status':data.get('status'),'strict_parity_passed':data.get('strict_parity_passed'),
                'assets':[{k:row.get(k) for k in ('symbol','status','orders_lean','orders_nautilus','matched_orders','months_compared',
                    'end_equity_lean_usd','end_equity_nautilus_usd','max_abs_monthly_equity_difference_usd')} for row in data['assets']],
                'verified_in_this_environment':False,'scope':'synthetic_monthly_close_fixture_only'}

    @app.get('/api/v2/research/catalog',dependencies=[Depends(admin)])
    def catalog():
        return {'plugins':[{'id':n,'input':'JSON','execution':'reference_only'} for n in ('sma_cross','ema_cross','rsi_reversion','buy_hold','trusted_python')],
                'python_plugins':'locally-reviewed SHA256 pinned only; no arbitrary upload',
                'data_sources':['sample (synthetic)','dataset (uploaded)','yahoo (optional worker dependency)','ccxt (optional worker dependency)'],
                'engines':['reference','lean_parity (AAPL/MSFT fixture)','nautilus_parity (AAPL/MSFT fixture)'],
                'paper':'client-quote simulation with risk guard','live':'disabled'}

    @app.get('/api/v2/research/performance/capabilities',dependencies=[Depends(admin)])
    def performance_capabilities():
        return {'timing':'Worker perf_counter_ns phase measurements; no hardware-certified SLA',
                'worker':'Local backoff 5-20s, remote backoff 30-90s',
                'robustness':'Bounded parameter grid + MC each set + train-only clusters + OOS diagnostics + expanding walk-forward',
                'support':['ema_cross','sma_cross','rsi_reversion'],
                'not_supported':['HFT latency guarantees','native generalized LEAN/Nautilus strategies','live brokerage orders'],
                'live_orders_enabled':False}

    @app.post('/api/v2/research/jobs',dependencies=[Depends(admin)],status_code=201)
    def create_job(req:JobSubmit,store=Depends(db)):
        try:result,reused=store.submit(req.kind,req.request.model_dump(mode='json'),req.idempotency_key)
        except ValueError as e:raise HTTPException(409,str(e)) from e
        return {**result,'reused':reused}

    @app.get('/api/v2/research/jobs',dependencies=[Depends(admin)])
    def list_jobs(store=Depends(db)):return store.list()

    @app.get('/api/v2/research/jobs/{id}',dependencies=[Depends(admin)])
    def get_job(id:str,store=Depends(db)):
        result=store.get(id,full=True)
        if not result:raise HTTPException(404,'Job not found')
        return result

    @app.post('/api/v2/research/jobs/{id}/cancel',dependencies=[Depends(admin)])
    def cancel_job(id:str,store=Depends(db)):
        if not store.cancel(id):raise HTTPException(409,'Job not queued/running')
        return {'status':'cancelled'}

    @app.post('/api/v2/research/jobs/{id}/retry',dependencies=[Depends(admin)])
    def retry_job(id:str,store=Depends(db)):
        if not store.retry(id):raise HTTPException(409,'Only failed jobs can be retried')
        return {'status':'queued','retry':'manual'}

    @app.post('/api/v2/worker/claim',dependencies=[Depends(worker)])
    def claim(r:Request,store=Depends(db)):
        owner=r.headers.get('x-worker-id','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,64}',owner):raise HTTPException(400,'Invalid worker id')
        kinds=('research','telegram_replay') if r.headers.get('x-worker-capabilities')=='hosted_reference' else None
        return {'job':store.claim(owner,allowed_kinds=kinds)}

    @app.post('/api/v2/worker/jobs/{id}/heartbeat',dependencies=[Depends(worker)])
    def heartbeat(id:str,body:Heartbeat,store=Depends(db)):
        if not store.heartbeat(id,body.owner):raise HTTPException(409,'Lease no longer valid')
        return {'status':'renewed'}

    @app.post('/api/v2/worker/jobs/{id}/finish',dependencies=[Depends(worker)])
    def finish(id:str,body:ResultPost,store=Depends(db)):
        try:ok=store.finish(id,body.owner,body.result,body.error)
        except ValueError as e:raise HTTPException(413,str(e)) from e
        if not ok:raise HTTPException(409,'Lease expired, cancelled or owned by another worker')
        return {'status':'accepted'}

    @app.post('/api/v2/research/datasets',dependencies=[Depends(admin)],status_code=201)
    def upload_data(body:DatasetUpload,store=Depends(db)):
        try:bars=normalize(body.bars)
        except (ValueError,TypeError) as e:raise HTTPException(422,str(e)) from e
        try:return store.store_dataset(bars,body.symbol)
        except ValueError as e:raise HTTPException(413,str(e)) from e

    @app.get('/api/v2/research/datasets',dependencies=[Depends(admin)])
    def datasets(store=Depends(db)):
        return store.list_datasets()

    @app.get('/api/v2/research/archives',dependencies=[Depends(admin)])
    def archives(store=Depends(db)):
        return store.list_archives()

    @app.get('/api/v2/worker/datasets/{id}',dependencies=[Depends(worker)])
    def worker_dataset(id:str,store=Depends(db)):
        d=store.get_dataset(id)
        if not d:raise HTTPException(404,'Unknown dataset')
        return d

    @app.get('/api/v2/research/telegram/legacy/status',dependencies=[Depends(admin)])
    def legacy_status(store=Depends(db)):
        return {'schema':'talos.legacy_telegram.status.v1',**store.legacy_stats(),
                'source':'legacy_v478_database', 'read_only':True}

    @app.post('/api/v2/research/telegram/legacy/batch',dependencies=[Depends(admin)],status_code=201)
    def legacy_batch(body:LegacyObservationUpload,store=Depends(db)):
        if body.schema_version!='talos.legacy_telegram.page.v1':raise HTTPException(422,'Unexpected bridge schema')
        try:return store.import_legacy_page(body.events)
        except ValueError as e:raise HTTPException(422,str(e)) from e

    @app.post('/api/v2/research/telegram/import',dependencies=[Depends(admin)],status_code=201)
    def upload_archive(body:ArchiveUpload,store=Depends(db)):
        try:return store.put_archive(body.source,normalize_archive(body.events))
        except ValueError as e:raise HTTPException(422,str(e)) from e

    @app.get('/api/v2/worker/telegram/{id}',dependencies=[Depends(worker)])
    def worker_archive(id:str,store=Depends(db)):
        a=store.get_archive(id)
        if a is None:raise HTTPException(404,'Archive not found')
        return {'events':a}

    @app.post('/api/v2/replay/phanes/import',dependencies=[Depends(admin)],status_code=201)
    def phanes_excel_import(body:PhanesWorkbookUpload,store=Depends(db)):
        # No direct bot-to-bot communication, scraping or Telegram token access.
        try:
            binary=base64.b64decode(body.xlsx_base64,validate=True)
            if len(binary)>MAX_XLSX_BYTES:raise ValueError('XLSX file exceeds 1.5MB')
            parsed=workbook_to_archive(binary,body.filename)
            # Store normalized source records as an immutable archive. Never
            # treat ATH/current market cap as verified PNL or native fills.
            persisted=store.put_archive('phanes_leaderboard_xlsx_manual',parsed['calls'])
            return {k:v for k,v in parsed.items() if k!='calls'} | {
                'archive_id':persisted['id'],'new_records_count':persisted['imported_count'],
                'preview':parsed['calls'][:5],
                'replay_ready':False,'integration_type':'manual_official_export',
                'native_engine_execution':False}
        except (ValueError,binascii.Error) as e:
            raise HTTPException(422,str(e)) from e

    @app.post('/api/v2/replay/verify',dependencies=[Depends(admin)])
    def cross_source_replay(body:VerificationRequest,store=Depends(db)):
        dataset=store.get_dataset(body.dataset_id)
        if dataset is None:raise HTTPException(404,'Unknown dataset: import historical OHLCV first')
        try:return verify(body,dataset)
        except ValueError as e:raise HTTPException(422,str(e)) from e

    @app.post('/api/v2/research/sentiment/vader',dependencies=[Depends(admin)])
    def sentiment_vader_batch(documents:list[TextDocument]):
        try:return {'schema':'talos.sentiment.vader_benchmark.v1','records':vader_batch(documents),
                    'source_authenticated':False,'live_orders_enabled':False}
        except ValueError as e:raise HTTPException(422,str(e)) from e
        except RuntimeError as e:raise HTTPException(503,str(e)) from e

    @app.post('/api/v2/replay/native-plan',dependencies=[Depends(admin)])
    def replay_engine_plan(body:NativePlanRequest,store=Depends(db)):
        dataset=store.get_dataset(body.dataset_id)
        if dataset is None:raise HTTPException(404,'Dataset not found')
        try:return build_plan(body,dataset)
        except ValueError as e:raise HTTPException(422,str(e)) from e

    @app.post('/api/v2/research/trend-sentiment/evaluate',dependencies=[Depends(admin)])
    def trend_sentiment_reference(body:TrendSentimentRequest,store=Depends(db)):
        dataset=store.get_dataset(body.dataset_id)
        if dataset is None:raise HTTPException(404,'Dataset not found')
        try:return evaluate(body,dataset)
        except ValueError as e:raise HTTPException(422,str(e)) from e

    @app.get('/api/v2/research/paper',dependencies=[Depends(admin)])
    def paper_state(store=Depends(db)):
        return {'account':paper_get(store),'type':'paper_only','live_orders_enabled':False,
                'mark_prices_are_client_supplied':True}

    @app.post('/api/v2/research/paper/order',dependencies=[Depends(admin)])
    def paper_order(body:PaperOrder,store=Depends(db)):
        try:return paper_action(store,body.side,body.symbol,body.qty,body.price,body.client_order_id)
        except (ValueError,PermissionError) as e:raise HTTPException(422,str(e)) from e

    @app.post('/api/v2/research/paper/kill',dependencies=[Depends(admin)])
    def kill_paper(store=Depends(db)):
        return paper_action(store,'KILL','',0,0)

    @app.post('/api/v2/research/live/orders',dependencies=[Depends(admin)])
    def live_orders_disabled():
        raise HTTPException(403,'Live orders disabled; no production broker has been authenticated or certified')

    webdir=Path(__file__).resolve().parents[1]/'web'
    @app.get('/talos-splash.png')
    def lab_logo():
        return FileResponse(webdir/'talos-splash.png',media_type='image/png')

    @app.get('/',response_class=HTMLResponse)
    @app.get('/lab',response_class=HTMLResponse)
    def lab():return (webdir/'index.html').read_text(encoding='utf-8')
    return app

# Lazy factory for uvicorn - no silent fallback to insecure default admin token.
app=create_app()
