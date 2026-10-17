"""Independent paper ledger; append-only receipts and source versions."""
import json,sqlite3,zlib,time
from engine import canonical,digest,fee,payout,day,reconstruct,SCENARIOS

class Store:
    def __init__(self,path,cfg):
        self.cfg=cfg;self.db=sqlite3.connect(path);self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS payloads(hash TEXT PRIMARY KEY,body BLOB);
        CREATE TABLE IF NOT EXISTS receipts(id INTEGER PRIMARY KEY,url TEXT,started REAL,received REAL,duration REAL,status INTEGER,headers TEXT,hash TEXT,error TEXT);
        CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY,series TEXT,source REAL,received REAL,receipt INTEGER,raw_hash TEXT,data TEXT,censored INTEGER,UNIQUE(series,source,raw_hash));
        CREATE TABLE IF NOT EXISTS publications(id INTEGER PRIMARY KEY,series TEXT,source REAL,received REAL,value REAL,receipt INTEGER,UNIQUE(series,source,value));
        CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY,event TEXT,series TEXT,source REAL,created REAL,reason TEXT,data TEXT,UNIQUE(event,source));
        CREATE TABLE IF NOT EXISTS cycles(id INTEGER PRIMARY KEY,created REAL,pre_receipt INTEGER,post_receipt INTEGER,data TEXT);
        CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY,decision INTEGER,scenario TEXT,event TEXT,series TEXT,created REAL,due REAL,status TEXT,reserve REAL,data TEXT,reason TEXT,UNIQUE(decision,scenario));
        CREATE TABLE IF NOT EXISTS book_checks(id INTEGER PRIMARY KEY,attempt INTEGER,phase INTEGER,receipt INTEGER,received REAL,data TEXT,reason TEXT);
        CREATE TABLE IF NOT EXISTS positions(id INTEGER PRIMARY KEY,attempt INTEGER UNIQUE,scenario TEXT,event TEXT,series TEXT,ticker TEXT,side TEXT,qty INTEGER,entered REAL,price REAL,fee REAL,friction REAL,cost REAL,settled REAL,payout REAL,net REAL,settlement_receipt INTEGER,UNIQUE(scenario,event));
        CREATE TABLE IF NOT EXISTS health(id INTEGER PRIMARY KEY,at REAL,kind TEXT,detail TEXT);
        ''')
        if self.meta('config_hash') and self.meta('config_hash')!=digest(cfg):raise RuntimeError('Frozen configuration mismatch')
        self.setmeta('config_hash',digest(cfg))
        if self.meta('started') is None:self.setmeta('started',time.time())
    def rows(self,sql,args=()):return [dict(x) for x in self.db.execute(sql,args)]
    def meta(self,k):
        r=self.db.execute('SELECT value FROM meta WHERE key=?',(k,)).fetchone();return json.loads(r[0]) if r else None
    def setmeta(self,k,v):
        with self.db:self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(k,canonical(v)))
    def log(self,kind,detail):
        with self.db:self.db.execute('INSERT INTO health(at,kind,detail) VALUES(?,?,?)',(time.time(),kind,str(detail)))
    def receipt(self,r):
        raw=canonical(r.get('body'));h=digest(r.get('body'))
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO payloads VALUES(?,?)',(h,zlib.compress(raw.encode())))
            q=self.db.execute('INSERT INTO receipts(url,started,received,duration,status,headers,hash,error) VALUES(?,?,?,?,?,?,?,?)',(r['url'],r['started'],r['received'],r['duration'],r['status'],canonical(r['headers']),h,r.get('error')))
        return q.lastrowid,h
    def ingest(self,series,r,configs,startup):
        prior=self.meta('source_poll_'+series);latest=None;counts={}
        with self.db:
            for point in r['body'].get('timeseries',[]):
                source=point['t']/1000
                if point.get('v') is not None:
                    self.db.execute('INSERT OR IGNORE INTO publications(series,source,received,value,receipt) VALUES(?,?,?,?,?)',(series,source,r['received'],float(point['v']),r['id']))
                data,why=reconstruct(point,configs,r['received'])
                if why:counts[why]=counts.get(why,0)+1;continue
                old=self.db.execute('SELECT 1 FROM sources WHERE series=? AND source=?',(series,source)).fetchone()
                censored=startup or not prior or r['received']-prior['received']>self.cfg['max_source_poll_gap']
                data.update(receipt=r['id'],received=r['received'],previous_poll=prior,first_seen_new_minute=not bool(old),censored=bool(censored))
                self.db.execute('INSERT OR IGNORE INTO sources(series,source,received,receipt,raw_hash,data,censored) VALUES(?,?,?,?,?,?,?)',(series,source,r['received'],r['id'],data['raw_hash'],canonical(data),int(censored)))
                # Pending readings first seen on a previous good poll remain usable;
                # startup/backfill never becomes an uncensored early signal later.
                first=self.db.execute('SELECT * FROM sources WHERE series=? AND source=? ORDER BY received,id LIMIT 1',(series,source)).fetchone()
                data['first_seen']=first['received'];data['first_seen_receipt']=first['receipt'];data['censored']=bool(first['censored'])
                if data['pending'] and not data['censored'] and 0<=r['received']-source<=self.cfg['max_source_age']:
                    if latest is None or source>latest['source_t']:latest=data
        self.setmeta('source_poll_'+series,{'received':r['received'],'started':r['started'],'receipt':r['id'],'counts':counts})
        return latest
    def decision(self,pick,event,series,source,created,reason,evidence):
        with self.db:
            c=self.db.execute('INSERT OR IGNORE INTO decisions(event,series,source,created,reason,data) VALUES(?,?,?,?,?,?)',(event,series,source,created,reason,canonical({'pick':pick,**evidence})))
        return c.lastrowid if c.rowcount else None
    def portfolio(self,scenario,now):
        positions=self.rows('SELECT * FROM positions WHERE scenario=?',(scenario,));settled=[p for p in positions if p['settled'] is not None];active=[p for p in positions if p['settled'] is None]
        pending=self.rows("SELECT * FROM attempts WHERE scenario=? AND status='pending'",(scenario,))
        cash=self.cfg['bankroll']-sum(p['cost'] for p in positions)+sum(p['payout'] for p in settled)
        reserved=sum(p['cost'] for p in active)+sum(a['reserve'] for a in pending)
        return {'cash':cash,'free_cash':cash-sum(a['reserve'] for a in pending),'reserved':reserved,
                'contracts':sum(p['qty'] for p in active)+len(pending)*self.cfg['contracts'],
                'daily':sum(p['net'] for p in settled if day(p['settled'])==day(now)),
                'active':active,'pending':pending,'settled':settled}
    def attempt(self,did,scenario,pick,now):
        qty=self.cfg['contracts'];friction=self.cfg['friction'][scenario]*qty;reserve=qty*pick['cap']+fee(pick['cap'],qty)+friction
        p=self.portfolio(scenario,now);reason=None
        owned=self.db.execute('SELECT 1 FROM positions WHERE scenario=? AND event=?',(scenario,pick['event'])).fetchone()
        if owned or any(x['series']==pick['series'] for x in p['active']+p['pending']):reason='owned_or_city_pending'
        elif p['contracts']+qty>self.cfg['max_contracts'] or p['reserved']+reserve>self.cfg['max_exposure']:reason='exposure'
        elif p['daily']-p['reserved']-reserve < -self.cfg['daily_budget']:reason='daily_risk'
        elif p['free_cash']-reserve < self.cfg['min_cash']:reason='cash'
        due=None if scenario=='canonical' else now+self.cfg['delays'][scenario]
        data={**pick,'friction':friction,'qty':qty}
        with self.db:
            c=self.db.execute('INSERT OR IGNORE INTO attempts(decision,scenario,event,series,created,due,status,reserve,data,reason) VALUES(?,?,?,?,?,?,?,?,?,?)',(did,scenario,pick['event'],pick['series'],now,due,'blocked' if reason else 'pending',reserve,canonical(data),reason))
        return c.lastrowid if c.rowcount and not reason else None
    def fail(self,aid,why):
        with self.db:self.db.execute("UPDATE attempts SET status='unfilled',reason=? WHERE id=? AND status='pending'",(why,aid))
    def fill(self,aid,price,received):
        with self.db:
            a=self.db.execute('SELECT * FROM attempts WHERE id=?',(aid,)).fetchone()
            if not a or a['status']!='pending':return False
            d=json.loads(a['data']);f=fee(price,d['qty']);cost=price*d['qty']+f+d['friction']
            assert price<=d['cap']+1e-8 and cost<=a['reserve']+1e-8 and received<d['close']
            self.db.execute('INSERT INTO positions(attempt,scenario,event,series,ticker,side,qty,entered,price,fee,friction,cost) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(aid,a['scenario'],a['event'],a['series'],d['ticker'],d['side'],d['qty'],received,price,f,d['friction'],cost))
            self.db.execute("UPDATE attempts SET status='filled' WHERE id=?",(aid,))
        return True
    def settle(self,m,r):
        with self.db:
            for p in self.db.execute('SELECT * FROM positions WHERE ticker=? AND settled IS NULL',(m['ticker'],)).fetchall():
                v=payout(m,p['side'])
                if v is not None:
                    gross=v*p['qty'];self.db.execute('UPDATE positions SET settled=?,payout=?,net=?,settlement_receipt=? WHERE id=? AND settled IS NULL',(r['received'],gross,gross-p['cost'],r['id'],p['id']))

def status(store):
    now=time.time();out={'mode':'PAPER ONLY','generated':now,'heartbeat':store.meta('heartbeat'),'started':store.meta('started'),'clock':store.meta('clock'),'config_hash':store.meta('config_hash'),'portfolios':{},'financial_orders':0}
    for scenario in SCENARIOS:
        p=store.portfolio(scenario,now);settled=sorted(p['settled'],key=lambda x:(x['settled'],x['id']));eq=peak=dd=0
        for row in settled:eq+=row['net'];peak=max(peak,eq);dd=max(dd,peak-eq)
        out['portfolios'][scenario]={'cash':round(p['cash'],4),'free_cash':round(p['free_cash'],4),'reserved':round(p['reserved'],4),'open':len(p['active']),'pending':len(p['pending']),'settled':len(settled),'wins':sum(x['payout']==x['qty'] for x in settled),'losses':sum(x['payout']==0 for x in settled),'net':round(eq,4),'realized_drawdown':round(dd,4),'fees':round(sum(x['fee'] for x in settled),4),'friction':round(sum(x['friction'] for x in settled),4),'attempt_status':store.rows('SELECT status,reason,COUNT(*) n FROM attempts WHERE scenario=? GROUP BY status,reason',(scenario,))}
    out['decisions']=store.rows('SELECT reason,COUNT(*) n FROM decisions GROUP BY reason')
    out['trades']=store.rows('SELECT scenario,event,ticker,side,qty,entered,price,fee,friction,settled,payout,net FROM positions ORDER BY id DESC LIMIT 100')
    out['sources']=store.rows('SELECT series,COUNT(*) versions,MAX(received) last_received,SUM(censored=0) uncensored FROM sources GROUP BY series')
    out['recent_errors']=store.rows('SELECT * FROM health ORDER BY id DESC LIMIT 15')
    out['source_polls']={s:store.meta('source_poll_'+s) for s in store.cfg['cities']}
    out['review']={'preliminary_ready':out['portfolios']['early']['settled']>=20,'main_ready':now-out['started']>=14*86400 and out['portfolios']['early']['settled']>=100,'entries_end':out['started']+store.cfg['stop_days']*86400}
    return out
