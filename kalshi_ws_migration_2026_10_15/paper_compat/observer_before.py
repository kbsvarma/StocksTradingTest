"""Isolated read-only live signal follower + PUBLIC GET-only paper execution.

No keys, authenticated client, portfolio endpoint or financial order methods.
Production SQLite is opened mode=ro/query_only. All writes stay in this folder.
"""
import argparse
import asyncio
import collections
import fcntl
import gzip
import hashlib
import json
import logging
import re
import signal
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from decimal import Decimal as D
import requests
from paper import canonical, validate_signal, resting_price, lane_for, route_for, book, ioc_depth, queue_ahead, fee, apply_trade, number, timestamp, risk_reason

P = Path(__file__).resolve().parent
LIVE = Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
BASE = 'https://api.elections.kalshi.com/trade-api/v2'
ALLOWED = re.compile(r'^/(?:markets/trades|markets/KX[A-Z0-9.-]+(?:/orderbook)?|series/KX[A-Z0-9]+15M)$')
LOG = logging.getLogger('resting-paper')

class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=3)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS signals(ticker TEXT PRIMARY KEY,event_id INTEGER,received REAL,record TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS orders(variant TEXT,ticker TEXT,record TEXT NOT NULL,PRIMARY KEY(variant,ticker));
        CREATE TABLE IF NOT EXISTS fills(id INTEGER PRIMARY KEY,variant TEXT,ticker TEXT,trade_id TEXT,ts REAL,record TEXT NOT NULL,UNIQUE(variant,ticker,trade_id));
        CREATE TABLE IF NOT EXISTS receipts(id INTEGER PRIMARY KEY,started REAL,received REAL,path TEXT,status INTEGER,digest TEXT,record BLOB);
        CREATE TABLE IF NOT EXISTS log(id INTEGER PRIMARY KEY,ts REAL,kind TEXT,ticker TEXT,detail TEXT);
        CREATE TABLE IF NOT EXISTS actual(ticker TEXT PRIMARY KEY,record TEXT NOT NULL);
        ''')
    def meta(self,key,default=None):
        x=self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        return json.loads(x[0]) if x else default
    def setmeta(self,key,value):
        self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,canonical(value)));self.db.commit()
    def log(self,kind,ticker='',detail=''):
        self.db.execute('INSERT INTO log(ts,kind,ticker,detail) VALUES(?,?,?,?)',(time.time(),kind,ticker,str(detail)));self.db.commit()
    def orders(self,variant=None):
        sql='SELECT record FROM orders'+(' WHERE variant=?' if variant else '')
        return [json.loads(r[0]) for r in self.db.execute(sql, (variant,) if variant else ())]
    def save(self,r):
        self.db.execute('INSERT OR REPLACE INTO orders VALUES(?,?,?)',(r['variant'],r['ticker'],canonical(r)));self.db.commit()
    def receipt(self,r):
        raw=canonical(r).encode(); digest=hashlib.sha256(raw).hexdigest()
        c=self.db.execute('INSERT INTO receipts(started,received,path,status,digest,record) VALUES(?,?,?,?,?,?)',(r['started'],r['received'],r['path'],r['status'],digest,gzip.compress(raw)))
        self.db.commit();return c.lastrowid

class Public:
    def __init__(self, rate):
        self.local=threading.local();self.lock=threading.Lock();self.next=0.;self.pause=0.;self.rate=rate
    def get(self,path,params):
        if not ALLOWED.fullmatch(path):raise ValueError('Public GET allowlist rejected '+path)
        if not hasattr(self.local,'session'):
            self.local.session=requests.Session();self.local.session.trust_env=False
            self.local.session.headers.update({'User-Agent':'Resting-PAPER/1.0','Cache-Control':'no-cache'})
        with self.lock:
            # Give the production minute-boundary scans priority; record this lag.
            wall=time.time();quiet=max(0,2-wall%60) if wall%60<2 else 0
            delay=max(quiet,self.next-time.monotonic(),self.pause-time.monotonic(),0)
            if delay:time.sleep(delay)
            self.next=time.monotonic()+1/self.rate
        start=time.time();body=None;error=None;status=0;headers={}
        try:
            r=self.local.session.get(BASE+path,params=params,timeout=(1,2),allow_redirects=False)
            status=r.status_code;headers={k:r.headers.get(k) for k in ('Date','Age','Cache-Control')}
            if status==429:self.pause=time.monotonic()+60
            r.raise_for_status()
            if status!=200:raise ValueError('Unexpected HTTP '+str(status))
            body=r.json()
        except (requests.RequestException,ValueError) as ex:error=str(ex)[:500]
        return dict(path=path,params=params,started=start,received=time.time(),status=status,headers=headers,error=error,body=body)

class Observer:
    def __init__(self,root=P):
        self.root=Path(root);self.cfg=json.loads((self.root/'frozen_config.json').read_text())
        assert self.cfg['paper_only'] is True and self.cfg['contracts']==5
        self.store=Store(self.root/'paper.sqlite');self.db=self.store.db
        config_hash=hashlib.sha256(canonical(self.cfg).encode()).hexdigest()
        if self.store.meta('config_hash',config_hash)!=config_hash:raise RuntimeError('Frozen config changed')
        self.store.setmeta('config_hash',config_hash)
        self.live=sqlite3.connect((LIVE/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=.1)
        self.live.execute('PRAGMA query_only=ON')
        self.client=Public(self.cfg['max_public_requests_per_second']);self.running=True;self.tasks={}
        self.fees=self.store.meta('series',{});self.healthy=False;self.block='initial_checks';self.clock={};self.lastclock=(time.time(),time.monotonic())
        if not self.store.meta('started'):
            self.store.setmeta('started',time.time())
            self.store.setmeta('event_cursor',self.live.execute('SELECT COALESCE(MAX(id),0) FROM events').fetchone()[0])
        self.cursor=self.store.meta('event_cursor');self.store.setmeta('boot',time.time())
        # An interrupted lifetime is not reconstructed as if continuously observed.
        for r in self.store.orders():
            if r['state'] in ('resting','waiting_control'):
                r.update(state='interrupted',coverage_complete=False,effective_expiry=min(r['effective_expiry'],time.time()),end_reason='observer_restart');self.store.save(r)
        self.store.log('boot',detail='PUBLIC GET ONLY; production SQLite query_only')
    async def get(self,path,params=None):
        r=await asyncio.to_thread(self.client.get,path,params or {})
        r['id']=self.store.receipt(r)
        # Series fees and finalized outcomes use a documented15s CDN cache.
        # Execution books/prints retain the strict one-second age limit.
        max_age=60 if path.startswith('/series/') or (path.startswith('/markets/KX') and not path.endswith('/orderbook')) else 1
        if r['error'] or r['received']-r['started']>3 or float(r['headers'].get('Age') or 0)>max_age:
            self.store.log('http_error',path,r['error'] or 'slow_or_cached_response')
            raise RuntimeError(r['error'] or 'slow_or_cached_response')
        return r
    def flag_guard(self, asset=None):
        # A reviewed asset pause restricts only that asset. Never accept a new
        # ARM flag, a removed pause, or another unreviewed production change.
        expected=dict(self.cfg['live_flags'])
        manifest=self.root/'reviewed_halt_flags.json'
        if manifest.exists():
            extra=json.loads(manifest.read_text())['flags']
            allowed={'HALT_'+a for a in self.cfg['assets']}
            if any(n not in allowed or n in expected for n in extra):
                return 'invalid_reviewed_halt_manifest'
            expected.update(extra)
        flags={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in (LIVE/'state').iterdir() if f.is_file() and f.name.startswith(('ARM','HALT'))}
        if flags!=expected:return 'production_flags_changed'
        if asset and 'HALT_'+asset in flags:return 'live_asset_halted:'+asset
        return ''
    def integrity(self):
        for n,h in self.cfg['frozen_source_hashes'].items():
            if hashlib.sha256((self.root/'frozen_source'/n).read_bytes()).hexdigest()!=h:return 'frozen_source_changed'
        for n,h in self.cfg['live_source_hashes'].items():
            if hashlib.sha256((LIVE/n).read_bytes()).hexdigest()!=h:return 'production_source_changed'
        block=self.flag_guard()
        if block:return block
        status=json.loads((LIVE/'state/status.json').read_text())
        if time.time()-status['ts']>15:return 'live_status_stale'
        if status.get('halted') or status.get('error') or not status.get('armed') or status.get('mode')!='LIVE':return 'live_not_healthy'
        return ''
    async def health(self):
        while self.running:
            try:
                wall,mono=time.time(),time.monotonic();jump=abs((wall-self.lastclock[0])-(mono-self.lastclock[1]));self.lastclock=(wall,mono)
                ntp=await asyncio.to_thread(subprocess.check_output,['timedatectl','show','-p','NTPSynchronized','--value'],text=True,timeout=3)
                self.clock=dict(ts=wall,synchronized=ntp.strip()=='yes',jump=jump)
                self.block=self.integrity()
                if not self.clock['synchronized'] or jump>1:self.block='clock_unhealthy'
                if (self.root/'HALT_PAPER').exists():self.block='manual_paper_halt'
                if not self.block and any(not self.fees.get(a,{}).get('valid') or wall-self.fees.get(a,{}).get('received',0)>25200 for a in self.cfg['assets']):self.block='fee_metadata_not_ready'
                self.healthy=not self.block
                self.store.setmeta('health',dict(**self.clock,block=self.block))
            except Exception as ex:self.healthy=False;self.block=str(ex)[:200];self.store.log('health_error',detail=self.block)
            await asyncio.sleep(10)
    async def metadata(self):
        while self.running:
            for asset in self.cfg['assets']:
                try:
                    r=await self.get('/series/KX'+asset+'15M');s=r['body']['series']
                    valid=s.get('fee_type') in ('quadratic','quadratic_with_maker_fees') and number(s.get('fee_multiplier',0))>0
                    self.fees[asset]=dict(valid=valid,fee_type=s.get('fee_type'),multiplier=s.get('fee_multiplier'),received=r['received'],receipt=r['id'])
                except Exception as ex:self.store.log('series_error',asset,ex)
            self.store.setmeta('series',self.fees)
            ready=all(self.fees.get(a,{}).get('valid') and time.time()-self.fees[a]['received']<25200 for a in self.cfg['assets'])
            await asyncio.sleep(21600 if ready else 30)
    def base_order(self,s,variant,now):
        return dict(variant=variant,ticker=s['ticker'],asset=s['asset'],lane=s['lane'],side=s['side'],route=route_for(s),reserve=str(lane_for(s).reserve),signal=s,created_ts=now,close_ts=s['close_ts'],state='skipped',filled='0',cost='0',fees='0',payout='0',pnl='0',settled=False,coverage_complete=True,fill_evidence=[])
    async def opportunity(self,s):
        ticker=s['ticker'];now=time.time();rules=lane_for(s);orders=[];admission_book=None
        try:
            if not self.healthy:raise RuntimeError('health:'+self.block)
            block=self.flag_guard(s['asset'])
            if block:raise RuntimeError(block)
            meta=self.fees.get(s['asset'],{})
            if not meta.get('valid') or now-meta.get('received',0)>25200:raise RuntimeError('fee_metadata_unavailable')
            admission_book=await self.get('/markets/'+ticker+'/orderbook',{'depth':100})
            block=self.flag_guard(s['asset'])
            if block:raise RuntimeError(block)
            own,asks,best=book(admission_book['body'],s['side'])
            accepted=admission_book['received']+self.cfg['acceptance_delay_seconds']
            expiry=min(accepted+self.cfg['rest_seconds'],s['decision_ts']+25,s['close_ts'])
            if accepted>=expiry:raise RuntimeError('admission_too_late')
            if best is None or best<rules.floor:raise RuntimeError('book_below_floor_or_empty')
            for variant in self.cfg['variants']:
                v=variant['name'];o=self.base_order(s,v,now);o.update(fee_meta=meta,admission_receipt=admission_book['id'],accepted_ts=accepted,effective_expiry=expiry,original_expiry=expiry,queue_remaining='0',queue_initial='0',price=None)
                reason=risk_reason(self.store.orders(v),s,now,self.cfg['paper_cash_by_route'])
                try:
                    if reason:raise ValueError(reason)
                    px=resting_price(s,variant['discount'])
                    if best<=px:raise ValueError('post_only_would_cross')
                    queue=queue_ahead(own,px)
                    o.update(price=str(px),queue_remaining=str(queue),queue_initial=str(queue),state='resting',source_to_accept_ms=(accepted-s.get('signal_qualified_ts',s['observed_ts']))*1000)
                except ValueError as ex:
                    o.update(end_reason=str(ex))
                    if str(ex)=='queue_depth_truncated':o['coverage_complete']=False
                self.store.save(o);orders.append(o)
            # Matched, synchronous first-book IOC comparator. Not a full clone of
            # production retries: its purpose is to isolate collector latency.
            control=self.base_order(s,'paper_ioc',now);control.update(fee_meta=meta,accepted_ts=accepted,effective_expiry=expiry,original_expiry=expiry,admission_receipt=admission_book['id'])
            reason=risk_reason(self.store.orders('paper_ioc'),s,now,self.cfg['paper_cash_by_route'])
            if reason:control['end_reason']=reason
            else:control['state']='waiting_control'
            self.store.save(control)
            def cancel_observation(reason):
                nonlocal expiry
                now=time.time()
                for o in orders:
                    if o['state']=='resting':
                        o.update(effective_expiry=min(o['effective_expiry'],now),end_reason='health_cancel:'+reason,coverage_complete=False);self.store.save(o)
                if control['state']=='waiting_control':
                    control.update(state='unfilled',end_reason='health_cancel:'+reason,coverage_complete=False);self.store.save(control)
                expiry=min(expiry,now)
            seen=set();lastbook=admission_book['received'];lasttrades=None;first_control=True;final_sweep=False
            # All flow receipts retained; a 15s publication grace only admits
            # trades timestamped inside the original order lifetime.
            while time.time()<expiry+self.cfg['trade_publication_grace_seconds'] and self.running:
                now=time.time()
                if now<expiry:
                    block=self.flag_guard(s['asset']) or (self.block if not self.healthy else '')
                    if block:cancel_observation(block)
                if time.time()<expiry:
                    try:
                        r=await self.get('/markets/'+ticker+'/orderbook',{'depth':100})
                        block=self.flag_guard(s['asset']) or (self.block if not self.healthy else '')
                        if block:cancel_observation(block)
                        own,asks,best=book(r['body'],s['side']);gap=r['received']-lastbook;lastbook=r['received']
                        for o in orders:
                            if gap>self.cfg['max_book_gap_seconds']:o['coverage_complete']=False;o['max_book_gap_seconds']=max(gap,o.get('max_book_gap_seconds',0))
                            if best is not None and best<rules.floor:
                                o.update(effective_expiry=min(o['effective_expiry'],r['received']+self.cfg['cancel_latency_seconds']),end_reason='floor_cancel_requested')
                            if o.get('price') and best is not None and best<=number(o['price']):o['quote_touch_observed']=True
                            self.store.save(o)
                        if first_control and r['started']>=accepted:
                            first_control=False
                            if control['state']=='waiting_control':
                                levels=ioc_depth(r['body'],s) if r['received']<control['effective_expiry'] else None
                                if levels:
                                    cost=sum(number(p)*number(q) for p,q in levels);fees=fee(levels,meta['multiplier'])
                                    control.update(state='filled',filled='5',cost=str(cost),fees=str(fees),price=str(cost/5),fill_ts=r['received'],fill_evidence=[dict(receipt=r['id'],levels=levels,kind='subsequent_displayed_depth')])
                                else:control.update(state='unfilled',end_reason='first_subsequent_book_not_executable')
                                self.store.save(control)
                    except Exception as ex:
                        for o in orders:o['coverage_complete']=False;self.store.save(o)
                        self.store.log('book_error',ticker,ex)
                # Refresh at most every two seconds; final sweep at expiry+12s.
                if lasttrades is None or (time.time()<expiry and time.time()-lasttrades>=2) or (time.time()>=expiry+12 and not final_sweep):
                    try:
                        cursor='';trades=[];pages=0;incomplete=False
                        while True:
                            params=dict(ticker=ticker,min_ts=int(accepted)-1,max_ts=int(expiry)+1,limit=1000,is_block_trade='false')
                            if cursor:params['cursor']=cursor
                            tr=await self.get('/markets/trades',params);pages+=1
                            trades.extend((x,tr['id'],tr['received']) for x in tr['body'].get('trades',[]))
                            cursor=tr['body'].get('cursor','')
                            if not cursor:break
                            if pages>=self.cfg['max_trade_pages']:incomplete=True;break
                        for x,rid,received in sorted(trades,key=lambda a:(a[0].get('created_time',''),a[0].get('trade_id',''))):
                            ident=x.get('trade_id')
                            if not ident or ident in seen:continue
                            seen.add(ident)
                            for o in orders:
                                if o['state']!='resting':continue
                                qty,reason=apply_trade(o,x)
                                if reason in ('invalid_trade','unknown_aggressor'):o['coverage_complete']=False
                                if qty>0:
                                    item=dict(trade_id=ident,quantity=str(qty),price=o['price'],exchange_ts=timestamp(x['created_time']),first_received=received,receipt=rid,reason=reason)
                                    o['fill_evidence'].append(item);o['fill_ts']=item['exchange_ts']
                                    levels=[[o['price'],o['filled']]];o['cost']=str(number(o['price'])*number(o['filled']));o['fees']=str(fee(levels,meta['multiplier'],True,meta['fee_type']))
                                    self.db.execute('INSERT OR IGNORE INTO fills(variant,ticker,trade_id,ts,record) VALUES(?,?,?,?,?)',(o['variant'],ticker,ident,item['exchange_ts'],canonical(item)));self.db.commit()
                                if incomplete:o['coverage_complete']=False
                                self.store.save(o)
                        if incomplete:
                            for o in orders:o['coverage_complete']=False;o['trade_pagination_incomplete']=True;self.store.save(o)
                        lasttrades=time.time()
                        if lasttrades>=expiry+12:final_sweep=True
                    except Exception as ex:
                        for o in orders:o['coverage_complete']=False;self.store.save(o)
                        self.store.log('trade_error',ticker,ex)
                await asyncio.sleep(.25 if time.time()<expiry else 1)
            for o in orders:
                if o['state']=='resting':
                    q=number(o['filled']);o.update(state='filled' if q==5 else 'partial' if q>0 else 'unfilled',end_reason=o.get('end_reason','expired'),coverage_complete=o['coverage_complete'] and final_sweep)
                    self.store.save(o)
            if control['state']=='waiting_control':control.update(state='unfilled',coverage_complete=False,end_reason='no_timely_control_book');self.store.save(control)
        except Exception as ex:
            self.store.log('opportunity_error',ticker,ex)
            for o in self.store.orders():
                if o['ticker']==ticker and o['state'] in ('resting','waiting_control'):
                    o.update(state='interrupted',coverage_complete=False,effective_expiry=min(o['effective_expiry'],time.time()),end_reason='opportunity_exception');self.store.save(o)
            for v in [x['name'] for x in self.cfg['variants']]+['paper_ioc']:
                if not self.db.execute('SELECT 1 FROM orders WHERE variant=? AND ticker=?',(v,ticker)).fetchone():
                    o=self.base_order(s,v,now);o.update(end_reason=str(ex),coverage_complete=False);self.store.save(o)
    async def signals(self):
        while self.running:
            try:
                rows=self.live.execute('SELECT id,ts,event FROM events WHERE id>? ORDER BY id LIMIT 200',(self.cursor,)).fetchall()
                for ident,saved,raw in rows:
                    self.cursor=ident;s=json.loads(raw);now=time.time()
                    if '429' in str(s.get('error','')):
                        self.client.pause=max(self.client.pause,time.monotonic()+60);self.store.log('yield_to_live_429',s.get('ticker',''),str(s.get('error',''))[:300])
                    if validate_signal(s):continue
                    if now>self.store.meta('started')+self.cfg['entry_days']*86400:continue
                    t=s['ticker']
                    prior=self.live.execute('SELECT record FROM orders WHERE ticker=?',(t,)).fetchone()
                    if prior and json.loads(prior[0]).get('created_ts',now)<self.store.meta('started'):
                        continue
                    if self.db.execute('SELECT 1 FROM signals WHERE ticker=?',(t,)).fetchone():continue
                    s=dict(s,paper_seen_ts=now,live_saved_ts=saved,live_event_id=ident)
                    self.db.execute('INSERT INTO signals VALUES(?,?,?,?)',(t,ident,now,canonical(s)));self.db.commit()
                    if now-saved>self.cfg['max_event_delivery_seconds'] or now-s['decision_ts']>25:
                        self.store.log('signal_arrived_late',t,canonical(dict(saved_lag=now-saved,age=now-s['decision_ts'])))
                        for v in [x['name'] for x in self.cfg['variants']]+['paper_ioc']:
                            o=self.base_order(s,v,now);o.update(end_reason='signal_arrived_late',coverage_complete=False);self.store.save(o)
                        continue
                    self.tasks[t]=asyncio.create_task(self.opportunity(s))
                if rows:self.store.setmeta('event_cursor',self.cursor)
            except Exception as ex:self.store.log('signal_error',detail=ex)
            await asyncio.sleep(.1)
    async def reconcile(self):
        while self.running:
            try:
                # Include every live fill since start, including collection failures.
                rawrows=self.live.execute("SELECT ticker,record FROM orders WHERE json_extract(record,'$.created_ts')>=?",(self.store.meta('started'),)).fetchall()
                for ticker,raw in rawrows:self.db.execute('INSERT OR REPLACE INTO actual VALUES(?,?)',(ticker,raw))
                self.db.commit()
                pending=collections.defaultdict(list)
                for o in self.store.orders():
                    if not o.get('outcome_recorded') and time.time()>o['close_ts']+30 and o['state']!='resting':pending[o['ticker']].append(o)
                for ticker,orders in pending.items():
                    r=await self.get('/markets/'+ticker);m=r['body']['market'];result=m.get('result')
                    if result not in ('yes','no') or m.get('status') not in ('settled','finalized'):continue
                    for o in orders:
                        qty=number(o['filled']);payout=qty if result==o['side'] else D(0)
                        o.update(outcome_recorded=True,result=result,settlement_receipt=r['id'],settled=qty>0,settled_ts=r['received'],payout=str(payout),pnl=str(payout-number(o['cost'])-number(o['fees'])))
                        if qty>0:
                            taker=number(o['fees']) if o['variant']=='paper_ioc' else fee([[o['price'],o['filled']]],o['fee_meta']['multiplier'])
                            o.update(pnl_taker_fee_stress=str(payout-number(o['cost'])-taker),pnl_after_1c_friction=str(payout-number(o['cost'])-number(o['fees'])-D('.01')*qty))
                        self.store.save(o)
            except Exception as ex:self.store.log('reconcile_error',detail=ex)
            await asyncio.sleep(30)
    async def status(self):
        while self.running:
            rows=self.store.orders();counts=collections.Counter((r['variant']+':'+r['state']) for r in rows)
            x=dict(ts=time.time(),mode='PAPER_ONLY',health=self.block,clock=self.clock,started=self.store.meta('started'),entry_cutoff=self.store.meta('started')+86400*self.cfg['entry_days'],signals=self.db.execute('SELECT count(*) FROM signals').fetchone()[0],counts=dict(counts),running_jobs=sum(not t.done() for t in self.tasks.values()),public_http_errors=self.db.execute("SELECT count(*) FROM log WHERE kind='http_error'").fetchone()[0],config_hash=self.store.meta('config_hash'))
            (self.root/'status.tmp').write_text(json.dumps(x,indent=2)+'\n');(self.root/'status.tmp').replace(self.root/'status.json')
            self.tasks={k:v for k,v in self.tasks.items() if not v.done()}
            await asyncio.sleep(2)
    async def run(self):
        tasks=[asyncio.create_task(f()) for f in (self.health,self.metadata,self.signals,self.reconcile,self.status)]
        loop=asyncio.get_running_loop()
        for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,lambda:setattr(self,'running',False))
        while self.running:await asyncio.sleep(.2)
        for task in tasks+list(self.tasks.values()):task.cancel()
        await asyncio.gather(*tasks,*self.tasks.values(),return_exceptions=True)
        self.store.log('shutdown');self.live.close();self.db.close()

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    lock=(P/'observer.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    asyncio.run(Observer().run())
