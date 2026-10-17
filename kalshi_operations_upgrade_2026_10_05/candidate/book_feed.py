"""Read-only market-data feed. Its only output is a scheduling hint.

The feed cannot access an order client or ledger. Every hint still goes through
the normal REST/account/price/depth/intent checks in the single execution writer.
"""
from decimal import Decimal as D
import json
import threading
import time

WS='wss://external-api-ws.kalshi.com/trade-api/ws/v2'
MAX_SOURCE_AGE=1.0
MAX_RECEIPT_AGE=.5

def allowed(ticker):
    return isinstance(ticker,str) and any(ticker.startswith('KX'+a+'15M-') for a in ('BTC','ETH','SOL','XRP'))

class Books:
    def __init__(self):
        self.reset()
    def reset(self):
        self.books={};self.seq={};self.receipts={};self.sources={};self.versions={}
    def apply(self,r,now):
        try:return self._apply(r,now)
        except Exception:
            self.reset();raise
    def _apply(self,r,now):
        kind=r.get('type')
        if kind not in ('orderbook_snapshot','orderbook_delta'):return None
        sid=r['sid'];seq=r['seq'];msg=r['msg'];ticker=msg['market_ticker']
        if not allowed(ticker):raise ValueError('Unexpected stream market')
        if not isinstance(seq,int) or (sid in self.seq and seq!=self.seq[sid]+1):
            self.reset();raise ValueError('Order-book sequence gap')
        self.seq[sid]=seq
        if kind=='orderbook_snapshot':
            book={}
            for side in ('yes','no'):
                levels={}
                for px,qty in msg.get(side+'_dollars_fp',[]):
                    px,qty=D(px),D(qty)
                    if not px.is_finite() or not qty.is_finite() or not 0<=px<=1 or qty<0 or px in levels:
                        raise ValueError('Invalid snapshot level')
                    if qty:levels[px]=qty
                book[side]=levels
            self.books[ticker]=book
        else:
            if ticker not in self.books:raise ValueError('Delta before snapshot')
            side=msg['side'];px=D(msg['price_dollars']);delta=D(msg['delta_fp'])
            if side not in ('yes','no') or not px.is_finite() or not delta.is_finite() or not 0<=px<=1:
                raise ValueError('Invalid delta')
            qty=self.books[ticker][side].get(px,D(0))+delta
            if qty<0:raise ValueError('Negative depth')
            if qty:self.books[ticker][side][px]=qty
            else:self.books[ticker][side].pop(px,None)
        source=r.get('sending_ts_ms')
        if not isinstance(source,(int,float)):raise ValueError('Missing source timestamp')
        if ticker in self.sources and source/1000<self.sources[ticker]:raise ValueError('Regressing source timestamp')
        self.sources[ticker]=source/1000;self.receipts[ticker]=now
        self.versions[ticker]=seq
        return ticker
    def fresh(self,ticker,now):
        return (ticker in self.books and
                0<=now-self.receipts.get(ticker,0)<=MAX_RECEIPT_AGE and
                0<=now-self.sources.get(ticker,0)<=MAX_SOURCE_AGE)
    def ready(self,ticker,side,floor,cap,count,now):
        if not self.fresh(ticker,now):return False
        need=D(count)
        for bid,qty in sorted(self.books[ticker]['no' if side=='yes' else 'yes'].items(),reverse=True):
            price=1-bid
            if price<floor:return False
            if price>cap:break
            need-=min(need,qty)
            if need==0:return True
        return False

class NullFeed:
    def begin(self,T):pass
    def watch(self,ticker,close):pass
    def arm(self,s):pass
    def hint(self,s):return None
    def wait(self,seconds):time.sleep(seconds)
    def close(self):pass
    def status(self):return dict(mode='REST_FALLBACK',connected=False)

class BookFeed:
    def __init__(self,headers,connector=None):
        self.headers=headers;self.connector=connector
        self.lock=threading.RLock();self.wake=threading.Event();self.stop=threading.Event()
        self.targets={};self.signals={};self.ready_state={};self.hints={}
        self.books=Books();self.generation=0;self.connected=False;self.error='';self.frames=0;self.reconnects=0
        self.thread=threading.Thread(target=self._run,name='kalshi-book-hints',daemon=True)
        self.thread.start()
    def begin(self,T):
        with self.lock:
            self.signals={};self.ready_state={};self.hints={}
            self.targets={k:v for k,v in self.targets.items() if v>T}
    def watch(self,ticker,close):
        if not allowed(ticker) or not isinstance(close,(int,float)):return
        with self.lock:
            # One current contract per asset, bounded to four subscriptions.
            self.targets={k:v for k,v in self.targets.items() if k.split('-')[0]!=ticker.split('-')[0]}
            self.targets[ticker]=close
    def arm(self,s):
        from price_rules import limit_price
        from lanes import lane_for
        rule=lane_for(s);key=(rule.name,s['ticker'],s['decision_ts'])
        with self.lock:
            self.signals[key]=(s['side'],rule.floor,limit_price(s)[1],rule.contracts)
            self._evaluate(s['ticker'],time.time())
    def _evaluate(self,ticker,now):
        for key,rule in self.signals.items():
            if key[1]!=ticker:continue
            ok=0<=now-key[2]<=25 and self.books.ready(ticker,*rule,now)
            if ok and not self.ready_state.get(key,False):
                self.hints[key]=(self.generation,self.books.versions[ticker],now)
                self.wake.set()
            self.ready_state[key]=ok
    def hint(self,s):
        key=(s.get('lane','cushion_z2'),s['ticker'],s['decision_ts'])
        with self.lock:
            rule=self.signals.get(key)
            if not rule or not self.connected or not 0<=time.time()-key[2]<=25:return None
            if not self.books.ready(key[1],*rule,time.time()):return None
            return self.hints.get(key)
    def wait(self,seconds):
        self.wake.wait(seconds);self.wake.clear()
    def status(self):
        with self.lock:
            now=time.time()
            return dict(mode='BOOK_HINTS_WITH_REST_VALIDATION',connected=self.connected,error=self.error,
                frames=self.frames,reconnects=self.reconnects,targets=sorted(self.targets),
                fresh_markets=[t for t in self.targets if self.books.fresh(t,now)])
    def close(self):
        self.stop.set();self.wake.set();self.thread.join(timeout=3)
    def _run(self):
        delay=1
        while not self.stop.is_set():
            with self.lock:targets=tuple(sorted(t for t,c in self.targets.items() if c>time.time()))
            if not targets:self.stop.wait(.2);continue
            try:
                if self.connector is None:
                    from websockets.sync.client import connect
                    connector=connect
                else:connector=self.connector
                with connector(WS,additional_headers=self.headers(),open_timeout=2,close_timeout=1,
                               ping_interval=10,ping_timeout=5,max_queue=32,max_size=2**20) as ws:
                    with self.lock:
                        self.books.reset();self.ready_state={};self.hints={};self.generation+=1
                        self.connected=True;self.error=''
                    ws.send(json.dumps({'id':1,'cmd':'subscribe','params':{'channels':['orderbook_delta'],'market_tickers':list(targets)}}))
                    while not self.stop.is_set():
                        with self.lock:current=tuple(sorted(t for t,c in self.targets.items() if c>time.time()))
                        if current!=targets:break
                        try:raw=ws.recv(timeout=.25)
                        except TimeoutError:continue
                        r=json.loads(raw)
                        if r.get('type')=='error':raise ValueError('Book subscription rejected')
                        with self.lock:
                            ticker=self.books.apply(r,time.time())
                            if ticker is not None:
                                if ticker not in targets:raise ValueError('Unsubscribed market frame')
                                self.frames+=1;self._evaluate(ticker,time.time());delay=1
            except Exception as exc:
                with self.lock:
                    self.connected=False;self.books.reset();self.ready_state={};self.hints={}
                    self.error=type(exc).__name__+': '+str(exc)[:120];self.reconnects+=1
                self.stop.wait(delay);delay=min(15,delay*2)
            finally:
                with self.lock:
                    self.connected=False;self.books.reset();self.ready_state={};self.hints={}

class CheckSchedule:
    """REST fallback every second; at most four earlier stream checks per key.

    The time-based allowance prevents stream bursts consuming all 24 checks
    early. Hints never bypass scan_ready, order ownership, or engine guards.
    """
    def __init__(self):self.last={};self.used={};self.fast={}
    def ready(self,key,checks,age,now,hint):
        last=self.last.get(key)
        if last is None:return 'initial'
        allowance=min(24,max(1,int(age-2)+2))
        if checks>=allowance:return None
        if now-last>=1:return 'rest_fallback'
        if (hint and hint!=self.used.get(key) and now-last>=.25 and
            self.fast.get(key,0)<4 and checks<allowance):return 'book_update'
        return None
    def checked(self,key,now,hint,reason):
        self.last[key]=now
        if reason=='book_update':self.fast[key]=self.fast.get(key,0)+1
        if hint:self.used[key]=hint

def make_feed(credentials):
    if credentials is None:return NullFeed()
    from kalshi_client import _sign
    def headers():
        ts=str(time.time_ns()//1_000_000)
        return {'KALSHI-ACCESS-KEY':credentials.key_id,'KALSHI-ACCESS-TIMESTAMP':ts,
                'KALSHI-ACCESS-SIGNATURE':_sign(credentials.private_key,ts+'GET'+'/trade-api/ws/v2')}
    return BookFeed(headers)
