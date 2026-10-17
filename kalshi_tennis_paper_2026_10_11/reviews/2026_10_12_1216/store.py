"""Durable receipt log and idempotent isolated paper portfolios."""
import sqlite3,json,zlib,time,hashlib
from engine import canonical,digest,plays,point_key,day,fee,payout
class Store:
 def __init__(self,path,cfg):
  self.cfg=cfg;self.db=sqlite3.connect(path);self.db.row_factory=sqlite3.Row
  self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
  CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
  CREATE TABLE IF NOT EXISTS payloads(hash TEXT PRIMARY KEY,body BLOB);
  CREATE TABLE IF NOT EXISTS receipts(id INTEGER PRIMARY KEY,url TEXT,started REAL,received REAL,duration REAL,status INTEGER,headers TEXT,hash TEXT,error TEXT);
  CREATE TABLE IF NOT EXISTS events(event TEXT PRIMARY KEY,series TEXT,start REAL,milestone TEXT,info TEXT,updated REAL,prior TEXT,last_poll REAL,done INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY,event TEXT,received REAL,receipt INTEGER,hash TEXT);
  CREATE INDEX IF NOT EXISTS snapshot_time ON snapshots(event,received);
  CREATE TABLE IF NOT EXISTS points(event TEXT,point_key TEXT,hash TEXT,source REAL,first_seen REAL,lower_delay REAL,upper_delay REAL,censored INTEGER,revision INTEGER,PRIMARY KEY(event,point_key,hash));
  CREATE TABLE IF NOT EXISTS decisions(event TEXT,boundary INTEGER,received REAL,reason TEXT,data TEXT,PRIMARY KEY(event,boundary));
  CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY,scenario TEXT,event TEXT,boundary INTEGER,created REAL,due REAL,status TEXT,reserve REAL,data TEXT,reason TEXT,UNIQUE(scenario,event,boundary));
  CREATE TABLE IF NOT EXISTS book_checks(id INTEGER PRIMARY KEY,attempt INTEGER,phase INTEGER,receipt INTEGER,received REAL,data TEXT,reason TEXT);
  CREATE TABLE IF NOT EXISTS positions(id INTEGER PRIMARY KEY,attempt INTEGER UNIQUE,scenario TEXT,event TEXT,ticker TEXT,side TEXT,entered REAL,price REAL,fee REAL,friction REAL,cost REAL,settled REAL,payout REAL,net REAL,settlement_receipt INTEGER,UNIQUE(scenario,event));
  CREATE TABLE IF NOT EXISTS health(id INTEGER PRIMARY KEY,at REAL,kind TEXT,event TEXT,detail TEXT);
  ''')
  h=digest(cfg);old=self.meta('config_hash')
  if old and old!=h:raise RuntimeError('Frozen config mismatch: a new experiment/database is required')
  self.setmeta('config_hash',h)
  if not self.meta('started'):self.setmeta('started',time.time())
 def meta(self,k):
  r=self.db.execute('SELECT value FROM meta WHERE key=?',(k,)).fetchone();return json.loads(r[0]) if r else None
 def setmeta(self,k,v):
  with self.db:self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(k,canonical(v)))
 def log(self,kind,event,detail):
  with self.db:self.db.execute('INSERT INTO health(at,kind,event,detail) VALUES(?,?,?,?)',(time.time(),kind,event,str(detail)))
 def receipt(self,r):
  body=canonical(r.get('body'));h=hashlib.sha256(body.encode()).hexdigest()
  with self.db:
   self.db.execute('INSERT OR IGNORE INTO payloads VALUES(?,?)',(h,zlib.compress(body.encode())))
   c=self.db.execute('INSERT INTO receipts(url,started,received,duration,status,headers,hash,error) VALUES(?,?,?,?,?,?,?,?)',(r['url'],r['started'],r['received'],r['duration'],r['status'],canonical(r['headers']),h,r.get('error')))
  return c.lastrowid,h
 def body(self,h):return json.loads(zlib.decompress(self.db.execute('SELECT body FROM payloads WHERE hash=?',(h,)).fetchone()[0]))
 def ingest_score(self,event,rid,h,received,request_started):
  prev=self.db.execute('SELECT last_poll FROM events WHERE event=?',(event,)).fetchone()[0];es=plays(self.body(h))
  first=self.db.execute('SELECT r.started FROM snapshots s JOIN receipts r ON r.id=s.receipt WHERE s.event=? ORDER BY s.id LIMIT 1',(event,)).fetchone()
  previous=self.db.execute('SELECT r.started FROM snapshots s JOIN receipts r ON r.id=s.receipt WHERE s.event=? ORDER BY s.id DESC LIMIT 1',(event,)).fetchone()
  with self.db:
   self.db.execute('INSERT INTO snapshots(event,received,receipt,hash) VALUES(?,?,?,?)',(event,received,rid,h))
   for e in es:
    k=point_key(e);eh=digest(e);source=float(e['wall_clock']);old=self.db.execute('SELECT 1 FROM points WHERE event=? AND point_key=? AND hash=?',(event,k,eh)).fetchone()
    if old:continue
    revision=bool(self.db.execute('SELECT 1 FROM points WHERE event=? AND point_key=?',(event,k)).fetchone())
    censored=prev is None or source<(first[0] if first else request_started) or received-prev>self.cfg['max_score_poll_gap'] or revision or source>received+2
    self.db.execute('INSERT INTO points VALUES(?,?,?,?,?,?,?,?,?)',(event,k,eh,source,received,max(0,(previous[0] if previous else request_started)-source),received-source,int(censored),int(revision)))
   self.db.execute('UPDATE events SET last_poll=? WHERE event=?',(received,event))
  return es
 def snapshot(self,event,boundary):
  r=self.db.execute('SELECT * FROM snapshots WHERE event=? AND received<=? ORDER BY received DESC,id DESC LIMIT 1',(event,boundary)).fetchone()
  return dict(r) if r else None
 def portfolio(self,scenario,now):
  rr=[dict(r) for r in self.db.execute('SELECT * FROM positions WHERE scenario=?',(scenario,))];pending=[dict(r) for r in self.db.execute("SELECT * FROM attempts WHERE scenario=? AND status='pending'",(scenario,))]
  settled=[r for r in rr if r['settled'] is not None];active=[r for r in rr if r['settled'] is None];cash=self.cfg['bankroll']-sum(r['cost'] for r in rr)+sum(r['payout'] for r in settled);reserved=sum(r['cost'] for r in active)+sum(r['reserve'] for r in pending)
  return dict(cash=cash,free_cash=cash-sum(r['reserve'] for r in pending),reserved=reserved,contracts=len(active)+len(pending),daily=sum(r['net'] for r in settled if day(r['settled'])==day(now)),settled=settled,active=active,pending=pending)
 def attempt(self,event,boundary,scenario,pick,now):
  cfg=self.cfg;friction=cfg['base_friction'] if scenario=='base' else cfg['stress_friction'];reserve=pick['cap']+fee(pick['cap'],pick['mult'])+friction
  with self.db:
   if self.db.execute('SELECT 1 FROM positions WHERE scenario=? AND event=?',(scenario,event)).fetchone() or self.db.execute("SELECT 1 FROM attempts WHERE scenario=? AND event=? AND status='pending'",(scenario,event)).fetchone():return None,'owned_or_pending'
   p=self.portfolio(scenario,now);reason=None
   if p['contracts']+1>cfg['max_contracts'] or p['reserved']+reserve>cfg['max_exposure']:reason='exposure'
   elif p['daily']-p['reserved']-reserve < -cfg['daily_budget']:reason='daily_risk'
   elif p['free_cash']-reserve<cfg['min_cash']:reason='cash'
   data={**pick,'friction':friction};c=self.db.execute('INSERT OR IGNORE INTO attempts(scenario,event,boundary,created,due,status,reserve,data,reason) VALUES(?,?,?,?,?,?,?,?,?)',(scenario,event,boundary,now,now+cfg['book_delays'][scenario],'blocked' if reason else 'pending',reserve,canonical(data),reason))
  return (c.lastrowid if c.rowcount and not reason else None),reason
 def fail(self,aid,reason):
  with self.db:self.db.execute("UPDATE attempts SET status='unfilled',reason=? WHERE id=? AND status='pending'",(reason,aid))
 def fill(self,aid,price,when):
  with self.db:
   a=self.db.execute('SELECT * FROM attempts WHERE id=?',(aid,)).fetchone()
   if not a or a['status']!='pending':return False
   d=json.loads(a['data']);assert price<=d['cap']+1e-8
   f=fee(price,d['mult']);cost=price+f+d['friction'];assert cost<=a['reserve']+1e-8
   self.db.execute('INSERT INTO positions(attempt,scenario,event,ticker,side,entered,price,fee,friction,cost) VALUES(?,?,?,?,?,?,?,?,?,?)',(aid,a['scenario'],a['event'],d['ticker'],d['side'],when,price,f,d['friction'],cost));self.db.execute("UPDATE attempts SET status='filled' WHERE id=?",(aid,))
  return True
 def settle(self,ticker,market,received,rid):
  with self.db:
   for r in self.db.execute('SELECT * FROM positions WHERE ticker=? AND settled IS NULL',(ticker,)).fetchall():
    v=payout(market,r['side'])
    if v is not None:self.db.execute('UPDATE positions SET settled=?,payout=?,net=?,settlement_receipt=? WHERE id=? AND settled IS NULL',(received,v,v-r['cost'],rid,r['id']))
