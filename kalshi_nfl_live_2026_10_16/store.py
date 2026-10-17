"""Isolated causal receipts and simulated/live intent ledgers; never reads other ledgers."""
import json,sqlite3,time,zlib
from core import canonical,digest,parse_plays,dec,risk_check,fee
class Store:
    def __init__(self,path,cfg):
        self.cfg=cfg;self.db=sqlite3.connect(path);self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS blobs(hash TEXT PRIMARY KEY,data BLOB);
        CREATE TABLE IF NOT EXISTS receipts(id INTEGER PRIMARY KEY,event TEXT,kind TEXT,received REAL,started REAL,duration REAL,status INTEGER,headers TEXT,hash TEXT,error TEXT);
        CREATE INDEX IF NOT EXISTS receipt_time ON receipts(event,kind,received);
        CREATE TABLE IF NOT EXISTS plays(event TEXT,id TEXT,version INTEGER,received REAL,wall REAL,lag REAL,clean INTEGER,reason TEXT,semantic TEXT,data TEXT,PRIMARY KEY(event,id,version));
        CREATE TABLE IF NOT EXISTS decisions(event TEXT,boundary INTEGER,received REAL,reason TEXT,data TEXT,PRIMARY KEY(event,boundary));
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,event TEXT,boundary INTEGER,mode TEXT,client_id TEXT UNIQUE,status TEXT,ticker TEXT,side TEXT,quantity TEXT,reserve TEXT,cost TEXT,fees TEXT,net TEXT,settled REAL,data TEXT,created REAL,UNIQUE(event,boundary,mode));
        CREATE TABLE IF NOT EXISTS checks(id INTEGER PRIMARY KEY,ts REAL,event TEXT,kind TEXT,data TEXT);
        ''')
        old=self.meta('config_hash');assert old in (None,digest(cfg)),'Frozen config changed'
        self.setmeta('config_hash',digest(cfg))
    def meta(self,k):
        r=self.db.execute('SELECT value FROM meta WHERE key=?',(k,)).fetchone();return json.loads(r[0]) if r else None
    def setmeta(self,k,v):
        with self.db:self.db.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,canonical(v)))
    def log(self,kind,event,data):
        with self.db:self.db.execute('INSERT INTO checks(ts,event,kind,data) VALUES(?,?,?,?)',(time.time(),event,kind,canonical(data)))
    def receipt(self,event,kind,r):
        body=r.get('body');h=digest(body) if body is not None else None
        with self.db:
            if h:self.db.execute('INSERT OR IGNORE INTO blobs VALUES(?,?)',(h,zlib.compress(canonical(body).encode())))
            cur=self.db.execute('INSERT INTO receipts(event,kind,received,started,duration,status,headers,hash,error) VALUES(?,?,?,?,?,?,?,?,?)',(event,kind,r['received'],r['started'],r['duration'],r['status'],canonical(r['headers']),h,r.get('error')))
        return cur.lastrowid
    def ingest(self,event,r):
        rid=self.receipt(event,'score',r)
        if r.get('error'):return rid
        previous=self.meta('last_score_'+event);gap=r['received']-previous if previous else None
        self.setmeta('last_score_'+event,r['received'])
        for p in parse_plays(r['body']):
            semantic=digest({k:p[k] for k in ('id','seq','wall','period','clock','home','away')})
            old=self.db.execute('SELECT * FROM plays WHERE event=? AND id=? ORDER BY version DESC LIMIT 1',(event,p['id'])).fetchone()
            if old and old['semantic']==semantic:continue
            lag=r['received']-p['wall'];reason='clean'
            if old:reason='revision'
            elif previous is None:reason='startup'
            elif gap>self.cfg['maximum_poll_gap_seconds']:reason='poll_gap'
            elif lag<-.25:reason='future_source_timestamp'
            version=old['version']+1 if old else 1
            p.update(received=r['received'],version=version,receipt=rid)
            with self.db:self.db.execute('INSERT INTO plays VALUES(?,?,?,?,?,?,?,?,?,?)',(event,p['id'],version,r['received'],p['wall'],lag,int(reason=='clean'),reason,semantic,canonical(p)))
        return rid
    def source_at(self,event,boundary):
        r=self.db.execute("SELECT * FROM receipts WHERE event=? AND kind='score' AND received<=? AND error IS NULL ORDER BY received DESC,id DESC LIMIT 1",(event,boundary)).fetchone()
        if not r or boundary-r['received']>self.cfg['maximum_poll_gap_seconds']:return [],None
        body=json.loads(zlib.decompress(self.db.execute('SELECT data FROM blobs WHERE hash=?',(r['hash'],)).fetchone()[0]))
        out=[]
        for p in parse_plays(body):
            semantic=digest({k:p[k] for k in ('id','seq','wall','period','clock','home','away')})
            v=self.db.execute('SELECT * FROM plays WHERE event=? AND id=? AND semantic=? AND received<=? ORDER BY version DESC LIMIT 1',(event,p['id'],semantic,boundary)).fetchone()
            if v:out.append(json.loads(v['data']))
        return out,dict(r)
    def reserve(self,pick,mode,now):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            rows=[dict(r) for r in self.db.execute('SELECT * FROM orders WHERE mode=?',(mode,))]
            if any(r['event']==pick['event'] and r['status'] in ('pending','filled','unknown','settled') for r in rows):self.db.rollback();return None,'already_owned'
            if any(r['event']==pick['event'] and r['boundary']==pick['boundary'] for r in rows):self.db.rollback();return None,'already_attempted'
            reserve=5*dec(pick['cap'])+fee(pick['cap'])+dec('.05')
            why=risk_check(rows,self.cfg,now,reserve)
            if why:self.db.rollback();return None,why
            client_id='nfl-'+digest([mode,pick['event'],pick['boundary']])[:32]
            cur=self.db.execute('INSERT INTO orders(event,boundary,mode,client_id,status,ticker,side,quantity,reserve,data,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(pick['event'],pick['boundary'],mode,client_id,'pending',pick['ticker'],pick['side'],'5',str(reserve),canonical(pick),now));self.db.commit();return cur.lastrowid,None
        except BaseException:self.db.rollback();raise
    def finish_simulated(self,ident,price,now,evidence):
        r=self.db.execute('SELECT * FROM orders WHERE id=?',(ident,)).fetchone()
        if not r or r['status']!='pending':return
        data=json.loads(r['data']);data.update(simulated=True,execution_evidence=evidence,filled_at=now,price=str(price))
        fees=fee(price);cost=5*dec(price)+fees+dec('.05')
        with self.db:self.db.execute("UPDATE orders SET status='filled',cost=?,fees=?,reserve=?,data=? WHERE id=? AND status='pending'",(str(cost),str(fees),str(cost),canonical(data),ident))
    def fail(self,ident,reason):
        r=self.db.execute('SELECT data FROM orders WHERE id=?',(ident,)).fetchone()
        if not r:return
        d=json.loads(r[0]);d['failure']=reason
        with self.db:self.db.execute("UPDATE orders SET status='zero',reserve='0',data=? WHERE id=? AND status='pending' AND mode='verify'",(canonical(d),ident))
    def settle(self,m,received):
        if m.get('status')!='finalized' or not m.get('settlement_ts'):return
        result=m.get('result')
        if result in ('yes','no'):yes=dec(1 if result=='yes' else 0)
        elif m.get('settlement_value_dollars') is not None:yes=dec(m['settlement_value_dollars'])
        else:return
        if not 0<=yes<=1:return
        for r in self.db.execute("SELECT * FROM orders WHERE ticker=? AND status='filled'",(m['ticker'],)).fetchall():
            payout=yes if r['side']=='yes' else 1-yes;net=dec(r['quantity'])*payout-dec(r['cost']);d=json.loads(r['data']);d['settlement']=m
            with self.db:self.db.execute("UPDATE orders SET status='settled',net=?,settled=?,reserve='0',data=? WHERE id=? AND status='filled'",(str(net),received,canonical(d),r['id']))
