"""Durable SQL transactional queue. Compatible with SQLite dev and Neon Postgres (psycopg)."""
from __future__ import annotations
import hashlib,json,os,uuid
from datetime import datetime,timedelta,timezone
from sqlalchemy import create_engine,MetaData,Table,Column,String,Integer,Text,DateTime,UniqueConstraint,select,update,func
from sqlalchemy.exc import IntegrityError

meta=MetaData()
job=Table('lab_jobs',meta,
    Column('id',String(36),primary_key=True),Column('kind',String(32),nullable=False),Column('request_json',Text,nullable=False),
    Column('request_sha',String(64),nullable=False),Column('idem_key',String(120),unique=True),
    Column('status',String(20),nullable=False),Column('attempts',Integer,nullable=False,default=0),
    Column('lease_owner',String(64)),Column('lease_until',DateTime(timezone=True)),
    Column('result_json',Text),Column('result_sha',String(64)),Column('error',Text),
    Column('created',DateTime(timezone=True),nullable=False),Column('updated',DateTime(timezone=True),nullable=False))
dataset=Table('lab_datasets',meta,Column('id',String(64),primary_key=True),Column('content_sha',String(64),nullable=False),
              Column('symbol',String(36)),Column('bars_json',Text,nullable=False),Column('created',DateTime(timezone=True),nullable=False))
# Added in v2.6 as a new table, so existing lab_datasets schema is untouched.
market_provenance=Table('lab_market_dataset_provenance',meta,
    Column('dataset_id',String(64),primary_key=True),
    Column('metadata_json',Text,nullable=False),
    Column('fetched',DateTime(timezone=True),nullable=False))
archive=Table('lab_archives',meta,Column('id',String(64),primary_key=True),Column('source',String(100),nullable=False),
              Column('content_sha',String(64),nullable=False),Column('body_json',Text,nullable=False),Column('created',DateTime(timezone=True),nullable=False))

# Append-only imported observations, keyed by the *original* RawEvent id.
# No source/bot tables from v4.7.8 are touched by the Research Plane.
legacy_observation=Table('lab_legacy_telegram_observations',meta,
    Column('legacy_id',Integer,primary_key=True),Column('content_sha',String(64),nullable=False),
    Column('event_json',Text,nullable=False),Column('imported',DateTime(timezone=True),nullable=False))

component=Table('lab_component_heartbeats',meta,
    Column('name',String(64),primary_key=True),Column('state_json',Text,nullable=False),
    Column('updated',DateTime(timezone=True),nullable=False))

def now():return datetime.now(timezone.utc)

def aware(t):
    return t.replace(tzinfo=timezone.utc) if t is not None and t.tzinfo is None else t

def engine():
    uri=os.getenv('TALOS_LAB_DATABASE_URL','sqlite:///./talos_lab.sqlite3')
    if uri.startswith('postgresql://') and 'psycopg' not in uri:uri=uri.replace('postgresql://','postgresql+psycopg://',1)
    if uri.startswith('sqlite:///'):
        e=create_engine(uri,connect_args={'check_same_thread':False},pool_pre_ping=True)
    else:e=create_engine(uri,pool_pre_ping=True,pool_recycle=240)
    return e

class Store:
    def __init__(self,url=None):
        self.db=create_engine(url,connect_args={'check_same_thread':False},pool_pre_ping=True) if url else engine()
        meta.create_all(self.db)
    def update_component(self,name,payload):
        if name not in ('telegram_bot','windows_worker','hosted_reference_worker'):raise ValueError('Unknown component')
        data=json.dumps(payload,sort_keys=True,allow_nan=False)
        t=now()
        with self.db.begin() as conn:
            from sqlalchemy import delete
            # Per-component state, not an event log. A quick durable status signal.
            conn.execute(delete(component).where(component.c.name==name))
            conn.execute(component.insert().values(name=name,state_json=data,updated=t))
        return {'status':'received','component':name,'updated':t.isoformat()}
    def component_status(self,name):
        with self.db.connect() as conn:
            row=conn.execute(select(component).where(component.c.name==name)).mappings().first()
        if not row:return {'status':'not_connected','last_seen':None}
        seen=aware(row['updated']);age=max(0,(now()-seen).total_seconds())
        return {'status':'online' if age<180 else 'stale', 'last_seen':seen.isoformat(),
                'age_seconds':round(age,1),**json.loads(row['state_json'])}
    def submit(self,kind,request,idem=None):
        blob=json.dumps(request,sort_keys=True,default=str,allow_nan=False)
        sha=hashlib.sha256(blob.encode()).hexdigest();jid=str(uuid.uuid4());t=now()
        try:
            with self.db.begin() as conn:
                if idem:
                    old=conn.execute(select(job).where(job.c.idem_key==idem)).mappings().first()
                    if old:
                        if old['request_sha']!=sha or old['kind']!=kind:
                            raise ValueError('Idempotency key reused with different payload')
                        return self._safe(old),True
                conn.execute(job.insert().values(id=jid,kind=kind,request_json=blob,request_sha=sha,idem_key=idem,
                    status='queued',attempts=0,created=t,updated=t))
        except IntegrityError:
            # New transaction: PostgreSQL aborts the previous transaction after
            # an IntegrityError. Do NOT query again inside the failed transaction.
            if not idem:raise
            with self.db.connect() as conn:
                old=conn.execute(select(job).where(job.c.idem_key==idem)).mappings().first()
            if not old or old['request_sha']!=sha or old['kind']!=kind:
                raise ValueError('Idempotency key collision')
            return self._safe(old),True
        return self.get(jid),False
    def _safe(self,row,include_result=False):
        keys=['id','kind','status','attempts','created','updated','error','request_sha','result_sha']
        d={k:(v.isoformat() if hasattr(v,'isoformat') else v) for k in keys if (v:=row.get(k)) is not None}
        if include_result and row.get('result_json'):
            d['result']=json.loads(row['result_json'])
        if include_result:d['request']=json.loads(row['request_json'])
        return d
    def get(self,jid,full=False):
        with self.db.connect() as conn:
            row=conn.execute(select(job).where(job.c.id==jid)).mappings().first()
        return self._safe(row,full) if row else None
    def list(self,limit=50):
        with self.db.connect() as conn:
            rows=conn.execute(select(job).order_by(job.c.created.desc()).limit(limit)).mappings().all()
        return [self._safe(row) for row in rows]
    def claim(self,owner,seconds=180,allowed_kinds=None):
        t=now()
        with self.db.begin() as conn:
            # Exhausted leases must not silently stay running forever.
            conn.execute(update(job).where(job.c.status=='running',job.c.attempts>=3,job.c.lease_until<t)
                .values(status='failed',error='Worker lease expired after 3 attempts',lease_owner=None,lease_until=None,updated=t))
        # Optimistic compare-and-swap WHERE status==queued or leased until elapsed.
        # Works on SQLite and PostgreSQL without relying on SKIP LOCKED.
        for _ in range(12):
            with self.db.begin() as conn:
                q_select=(select(job.c.id,job.c.status,job.c.attempts,job.c.lease_until)
                    .where((job.c.status=='queued') | ((job.c.status=='running') & (job.c.lease_until<t)))
                    .where(job.c.attempts<3))
                if allowed_kinds is not None:
                    q_select=q_select.where(job.c.kind.in_(tuple(allowed_kinds)))
                candidate=conn.execute(q_select.order_by(job.c.created).limit(1)).first()
                if not candidate:return None
                jid,state,attempts,until=candidate
                q=update(job).where(job.c.id==jid).where(job.c.status==state).where(job.c.attempts==attempts)
                if state=='running':q=q.where(job.c.lease_until==until)
                changed=conn.execute(q.values(status='running',attempts=attempts+1,lease_owner=owner,
                    lease_until=t+timedelta(seconds=seconds),updated=t))
                if changed.rowcount==1:
                    row=conn.execute(select(job).where(job.c.id==jid)).mappings().first()
                    return {'id':jid,'kind':row['kind'],'request':json.loads(row['request_json']),
                            'attempt':row['attempts'],'lease_seconds':seconds}
        return None
    def heartbeat(self,jid,owner,seconds=180):
        with self.db.begin() as conn:
            result=conn.execute(update(job).where(job.c.id==jid,job.c.status=='running',job.c.lease_owner==owner,
                job.c.lease_until>now()).values(lease_until=now()+timedelta(seconds=seconds),updated=now()))
            return bool(result.rowcount)
    def finish(self,jid,owner,result,error=None):
        blob=json.dumps(result,allow_nan=False,default=str) if result is not None else None
        if blob and len(blob)>2_000_000:raise ValueError('Result too large (2MB limit)')
        with self.db.begin() as conn:
            row=conn.execute(select(job.c.status,job.c.lease_owner,job.c.lease_until).where(job.c.id==jid)).first()
            if not row or row[0]!='running' or row[1]!=owner or aware(row[2])<now():return False
            sha=hashlib.sha256(blob.encode()).hexdigest() if blob else None
            conn.execute(update(job).where(job.c.id==jid,job.c.status=='running',job.c.lease_owner==owner)
                .values(status='complete' if error is None else 'failed',result_json=blob,
                        result_sha=sha,error=(error[:1000] if error else None),lease_owner=None,
                        lease_until=None,updated=now()))
            return True
    def cancel(self,jid):
        with self.db.begin() as conn:
            updated=conn.execute(update(job).where(job.c.id==jid,job.c.status.in_(['queued','running']))
                .values(status='cancelled',lease_owner=None,lease_until=None,updated=now()))
            return bool(updated.rowcount)
    def retry(self,jid):
        with self.db.begin() as conn:
            updated=conn.execute(update(job).where(job.c.id==jid,job.c.status=='failed')
                .values(status='queued',attempts=0,lease_owner=None,lease_until=None,
                        result_json=None,result_sha=None,error=None,updated=now()))
            return bool(updated.rowcount)
    def store_dataset(self,bars,symbol,provenance=None):
        body=json.dumps(bars,sort_keys=True,allow_nan=False,separators=(',',':'))
        if len(body)>3_500_000:raise ValueError('Dataset > 3.5MB limit')
        sha=hashlib.sha256(body.encode()).hexdigest();t=now()
        with self.db.begin() as conn:
            if not conn.execute(select(dataset.c.id).where(dataset.c.id==sha)).first():
                conn.execute(dataset.insert().values(id=sha,content_sha=sha,symbol=symbol,bars_json=body,created=t))
            if provenance is not None:
                meta_body=json.dumps(provenance,sort_keys=True,allow_nan=False)
                if len(meta_body)>8000:raise ValueError('Dataset provenance exceeds limit')
                if not conn.execute(select(market_provenance.c.dataset_id).where(market_provenance.c.dataset_id==sha)).first():
                    conn.execute(market_provenance.insert().values(dataset_id=sha,metadata_json=meta_body,fetched=t))
        return {'dataset_id':sha,'count':len(bars),'sha256':sha}
    def list_datasets(self,limit=50):
        with self.db.connect() as conn:
            rows=conn.execute(select(dataset.c.id,dataset.c.symbol,dataset.c.created,dataset.c.bars_json)
                .order_by(dataset.c.created.desc()).limit(limit)).mappings().all()
        with self.db.connect() as conn:
            metadata={r['dataset_id']:json.loads(r['metadata_json']) for r in
                      conn.execute(select(market_provenance)
                                   .where(market_provenance.c.dataset_id.in_([x['id'] for x in rows])))
                          .mappings().all()} if rows else {}
        return [{'dataset_id':row['id'],'symbol':row['symbol'],
                 'bars':len(json.loads(row['bars_json'])),
                 'created':row['created'].isoformat(),
                 'provenance':metadata.get(row['id'],{'source':'USER_CSV_UNVERIFIED_MARKET_PROVENANCE','verified':False})} for row in rows]

    def list_archives(self,limit=40):
        with self.db.connect() as conn:
            rows=conn.execute(select(archive.c.id,archive.c.source,archive.c.created)
                .order_by(archive.c.created.desc()).limit(limit)).mappings().all()
        return [{'id':row['id'],'source':row['source'],
                 'created':row['created'].isoformat()} for row in rows]

    def get_dataset(self,id):
        with self.db.connect() as conn:row=conn.execute(select(dataset).where(dataset.c.id==id)).mappings().first()
        if row is None:return None
        with self.db.connect() as conn:
            meta_row=conn.execute(select(market_provenance.c.metadata_json)
                .where(market_provenance.c.dataset_id==id)).first()
        provenance=json.loads(meta_row[0]) if meta_row else {'source':'USER_CSV_UNVERIFIED_MARKET_PROVENANCE','verified':False}
        return {'id':row['id'],'symbol':row['symbol'],'bars':json.loads(row['bars_json']),
                'provenance':provenance}
    def put_archive(self,source,content):
        blob=json.dumps(content,sort_keys=True,ensure_ascii=False,allow_nan=False)
        if len(blob)>1_500_000:raise ValueError('Archive >1.5MB chunk limit')
        sha=hashlib.sha256(blob.encode()).hexdigest()
        with self.db.begin() as conn:
            if not conn.execute(select(archive.c.id).where(archive.c.id==sha)).first():
                conn.execute(archive.insert().values(id=sha,source=source,content_sha=sha,body_json=blob,created=now()))
        return {'id':sha,'sha256':sha,'imported_count':len(content)}
    def import_legacy_page(self, events):
        """Durable, idempotent legacy observation ingestion; no Telegram send side effects.

        Returns a content-addressed snapshot of the last 3000 observed events.
        Event-time is legacy first-known time, not historical publication time.
        """
        from core.telegram import normalize_archive
        if not isinstance(events,list) or len(events)>150:
            raise ValueError('Up to 150 events per bridge request')
        ids=[];payload=[]
        for event in events:
            if not isinstance(event,dict):raise ValueError('Event must be object')
            lid=event.get('legacy_id')
            if isinstance(lid,bool) or not isinstance(lid,int) or lid<=0:raise ValueError('legacy_id must be positive integer')
            ids.append(lid)
            payload.append({key:event[key] for key in ('chat_id','message_id','revision','timestamp','event_type','text')})
        if len(set(ids))!=len(ids):raise ValueError('Duplicate legacy ID in request')
        canonical=normalize_archive(payload)
        # normalize_archive sorts; pair by stable unique revision, not list index.
        by_key={(p['chat_id'],p['message_id'],p['revision']):p for p in canonical}
        ordered=[]
        for lid,rec in zip(ids,payload):
            key=(str(rec['chat_id']),str(rec['message_id']),str(rec['revision']))
            ordered.append((lid,by_key[key]))
        inserted=0
        with self.db.begin() as conn:
            for legacy_id, event in ordered:
                blob=json.dumps(event,sort_keys=True,ensure_ascii=False,separators=(',',':'))
                sha=hashlib.sha256(blob.encode()).hexdigest()
                existing=conn.execute(select(legacy_observation.c.content_sha)
                                      .where(legacy_observation.c.legacy_id==legacy_id)).scalar()
                if existing is not None:
                    if existing!=sha:raise ValueError(f'Legacy event #{legacy_id} changed: immutable collision')
                else:
                    # Atomic ignore-on-conflict for multiple concurrent importers;
                    # verify stored SHA after conflict to detect changed history.
                    dialect=conn.dialect.name
                    if dialect=='postgresql':
                        from sqlalchemy.dialects.postgresql import insert as native_insert
                    elif dialect=='sqlite':
                        from sqlalchemy.dialects.sqlite import insert as native_insert
                    else:
                        raise ValueError('Legacy bridge requires SQLite or PostgreSQL')
                    statement=native_insert(legacy_observation).values(
                        legacy_id=legacy_id,content_sha=sha,event_json=blob,imported=now())
                    outcome=conn.execute(statement.on_conflict_do_nothing(index_elements=['legacy_id']))
                    inserted+=int(outcome.rowcount==1)
                    if outcome.rowcount==0:
                        actual=conn.execute(select(legacy_observation.c.content_sha)
                           .where(legacy_observation.c.legacy_id==legacy_id)).scalar()
                        if actual!=sha:raise ValueError(f'Legacy event #{legacy_id} changed: immutable collision')
        # Query latest bounded window in event-id order then sort point-in-time.
        with self.db.connect() as conn:
            rows=conn.execute(select(legacy_observation.c.event_json)
                .order_by(legacy_observation.c.legacy_id.desc()).limit(3000)).all()
            cursor=conn.execute(select(func.max(legacy_observation.c.legacy_id))).scalar() or 0
            total=conn.execute(select(func.count(legacy_observation.c.legacy_id))).scalar() or 0
        snapshot=normalize_archive([json.loads(r[0]) for r in rows])
        arch=self.put_archive('legacy_v478_read_only_snapshots',snapshot) if snapshot else None
        return {'schema':'talos.legacy_telegram.sync.v1','inserted':inserted,
                'received':len(events),'stored_total':total,'last_legacy_id':cursor,
                'archive_id':arch['id'] if arch else None,
                'window_limited':total>3000,'snapshot_count':len(snapshot),
                'notifications_sent':False,'source_read_only':True}

    def legacy_stats(self):
        with self.db.connect() as conn:
            return {'cursor':conn.execute(select(func.max(legacy_observation.c.legacy_id))).scalar() or 0,
                    'count':conn.execute(select(func.count(legacy_observation.c.legacy_id))).scalar() or 0}

    def get_archive(self,id):
        with self.db.connect() as conn:row=conn.execute(select(archive).where(archive.c.id==id)).mappings().first()
        return json.loads(row['body_json']) if row else None

# Persistent isolated paper ledger, entirely distinct from the legacy v4.7.8 paper account.
paper=Table('lab_paper',meta,
    Column('id',String(36),primary_key=True),Column('state_json',Text,nullable=False),
    Column('version',Integer,nullable=False),Column('updated',DateTime(timezone=True),nullable=False))


def paper_get(store,id='default'):
    from core.brokers import PaperBook
    with store.db.begin() as conn:
        row=conn.execute(select(paper).where(paper.c.id==id)).mappings().first()
        if not row:
            body={'cash':'10000','positions':{},'fills':[],'killed':False}
            try:conn.execute(paper.insert().values(id=id,state_json=json.dumps(body),version=0,updated=now()))
            except IntegrityError:pass
            return body
        return json.loads(row['state_json'])

def paper_action(store,side,symbol,qty,price,client_order_id=None):
    from core.brokers import PaperBook
    from decimal import Decimal
    for _ in range(3):
        with store.db.begin() as conn:
            row=conn.execute(select(paper).where(paper.c.id=='default')).mappings().first()
            if row is None:
                conn.execute(paper.insert().values(id='default',state_json=json.dumps({'cash':'10000','positions':{},'fills':[],'killed':False}),version=0,updated=now()))
                continue
            data=json.loads(row['state_json'])
            book=PaperBook(cash=Decimal(data['cash']),positions=dict(data['positions']),fills=list(data['fills']),killed=data['killed'])
            if client_order_id:
                for old in book.fills:
                    if old.get('client_order_id')==client_order_id:
                        if old['symbol']!=symbol or old['side']!=side or int(old['qty'])!=int(qty) or Decimal(old['price'])!=Decimal(str(price)):
                            raise ValueError('client_order_id reused with different order payload')
                        return {'state':data,'fill':old,'version':row['version'],'reused':True}
            if side=='KILL':book.killed=True;fill=None
            else:
                fill=book.order(symbol,side,qty,price)
                fill['client_order_id']=client_order_id
            new={'cash':str(book.cash),'positions':book.positions,'fills':book.fills[-500:], 'killed':book.killed}
            changed=conn.execute(update(paper).where(paper.c.id=='default',paper.c.version==row['version']).values(
                state_json=json.dumps(new),version=row['version']+1,updated=now()))
            if changed.rowcount==1:return {'state':new,'fill':fill,'version':row['version']+1}
    raise RuntimeError('Paper portfolio update conflict')
