"""Small append-only local event archive, bot cursor and notification deduplication."""
from __future__ import annotations
import json,sqlite3
from datetime import datetime,timezone
from pathlib import Path

def utcnow():return datetime.now(timezone.utc).isoformat()

class BotDB:
    def __init__(self,path):
        p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
        self.conn=sqlite3.connect(str(p),timeout=10)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS channel_events(update_id INTEGER PRIMARY KEY,ts TEXT NOT NULL,
            source TEXT NOT NULL,body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tracked_jobs(job_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,
            last_status TEXT NOT NULL,notified TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS verified_outcomes(
            outcome_id TEXT PRIMARY KEY,post_id TEXT NOT NULL,symbol TEXT NOT NULL,
            author TEXT NOT NULL,verdict TEXT NOT NULL,return_pct REAL NOT NULL,
            method TEXT NOT NULL,verified_at TEXT NOT NULL);
        ''')
        self.conn.commit()
    def offset(self):return int(self.meta('offset','0'))
    def meta(self,key,default=''):
        row=self.conn.execute('SELECT v FROM meta WHERE k=?',(key,)).fetchone()
        return row[0] if row else default
    def set(self,key,value):
        with self.conn:self.conn.execute('INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',(key,str(value)))
    def save_update(self,update_id):
        with self.conn:
            self.conn.execute('INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v',('offset',str(update_id+1)))
    def ingest(self,update_id,source,event):
        payload=json.dumps(event,sort_keys=True,ensure_ascii=False)
        with self.conn:
            row=self.conn.execute('INSERT OR IGNORE INTO channel_events(update_id,ts,source,body) VALUES(?,?,?,?)',
                (int(update_id),utcnow(),source,payload))
        return row.rowcount==1
    def count(self):return self.conn.execute('SELECT count(*) FROM channel_events').fetchone()[0]
    def last_events(self,limit=500):
        records=self.conn.execute('SELECT body FROM channel_events ORDER BY update_id DESC LIMIT ?', (int(limit),)).fetchall()
        return [json.loads(x[0]) for x in reversed(records)]
    def archive_payload(self,max_count=500,max_bytes=1_200_000):
        data=self.last_events(max_count)
        while data and len(json.dumps(data,ensure_ascii=False).encode('utf-8'))>max_bytes:
            data=data[1:]
        return data
    def track_job(self,job_id,owner_id,status='queued'):
        with self.conn:self.conn.execute('INSERT OR IGNORE INTO tracked_jobs(job_id,owner_id,last_status) VALUES(?,?,?)',
            (str(job_id),str(owner_id),status))
    def pending_jobs(self):
        return self.conn.execute("SELECT job_id,owner_id,last_status FROM tracked_jobs WHERE last_status NOT IN ('complete','failed','cancelled') LIMIT 20").fetchall()
    def transition_job(self,job_id,new_status):
        with self.conn:
            row=self.conn.execute('UPDATE tracked_jobs SET last_status=? WHERE job_id=? AND last_status!=?',
                (new_status,job_id,new_status))
        return row.rowcount==1

    def record_verification(self,records, symbol, horizon):
        """Store price-scored research observations, not real executions.
        A manual replay never creates Telegram notifications.
        """
        import hashlib
        inserted=0
        for rec in records:
            if rec.get('outcome') not in ('WIN','LOSS','FLAT'):continue
            key='|'.join([str(rec.get('post_id')),str(symbol),str(horizon),str(rec.get('entry_date'))])
            oid=hashlib.sha256(key.encode()).hexdigest()
            with self.conn:
                row=self.conn.execute('INSERT OR IGNORE INTO verified_outcomes(outcome_id,post_id,symbol,author,verdict,return_pct,method,verified_at) VALUES(?,?,?,?,?,?,?,?)',
                    (oid,str(rec.get('post_id')),str(symbol),str(rec.get('author','unknown')),
                     rec['outcome'],float(rec.get('net_return_pct',0)),
                     str(rec.get('policy','price-only; not executed')),utcnow()))
                inserted+=int(row.rowcount==1)
        return inserted

    def outcome_summary(self,limit=12):
        rows=self.conn.execute('SELECT post_id,symbol,author,verdict,return_pct,method FROM verified_outcomes ORDER BY verified_at DESC LIMIT ?',
                               (int(limit),)).fetchall()
        counts=dict(self.conn.execute('SELECT verdict,COUNT(*) FROM verified_outcomes GROUP BY verdict').fetchall())
        return {'counts':counts,'records':[{'post_id':x[0],'symbol':x[1],'author':x[2],
               'outcome':x[3],'return_pct':x[4],'method':x[5]} for x in rows]}
