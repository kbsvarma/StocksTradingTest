"""Bounded PUBLIC-GET-only research ledger. No credentials, accounts or orders.

Two virtual controls: fixed BTC-first threshold1c with deadline, and identical
signals with no forced fallback. Not a live-bot package, no live mode exists.
"""
from pathlib import Path
import argparse, datetime as dt, hashlib, json, math, signal, sqlite3, time
import requests
import features

P=Path(__file__).resolve().parent;STATE=P/'paper_state';BASE='https://external-api.kalshi.com/trade-api/v2';STOP=False
def timestamp(v):return dt.datetime.fromisoformat(v.replace('Z','+00:00')).timestamp()
def fee(p,n=1):return math.ceil(.07*p*(1-p)*n*10000-1e-9)/10000
def atomic(path,obj):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(obj,indent=2,allow_nan=False));tmp.replace(path)
class Public:
    def __init__(self):self.session=requests.Session()
    def get(self,path,params=None):
        if not path.startswith('/') or any(x in path for x in ('portfolio','orders','account')):raise ValueError('Public market data only')
        r=self.session.get(BASE+path,params=params,timeout=8);r.raise_for_status();return r.json()
    def spot(self,asset,T):
        fmt=lambda t:dt.datetime.fromtimestamp(t,dt.timezone.utc).isoformat()
        r=self.session.get('https://api.exchange.coinbase.com/products/'+asset+'-USD/candles',
            params=dict(granularity=60,start=fmt(T-65*60),end=fmt(T)),timeout=8);r.raise_for_status()
        raw=r.json();return {int(x[0]):float(x[4]) for x in raw if int(x[0])<T},raw
def price(book,side,quantity=1):
    b=book.get('orderbook_fp',book.get('orderbook',{}));other='no' if side==1 else 'yes'
    levels=b.get(other+'_dollars',b.get(other,[]))
    normalized=[]
    for p,q in levels:
        p=float(p);q=float(q)
        if other+'_dollars' not in b:p/=100
        if q>0:normalized.append((1-p,q))
    left=quantity;cost=0;rawfee=0
    for p,q in sorted(normalized):
        if not 0<p<1:continue
        n=min(left,q);cost+=n*p;rawfee+=.07*n*p*(1-p);left-=n
        if left<=1e-8:return dict(price=cost/quantity,fee=math.ceil(rawfee*10000-1e-9)/10000,quantity=quantity)
    return None
def offer(f,model):
    p=features.probability(f,model);ey=p-f['a']-fee(f['a'])-.0025;en=1-p-(1-f['b'])-fee(1-f['b'])-.0025
    dr,ev=(1,ey) if ey>=en else (-1,en)
    return dict(side=dr,edge=ev,probability_yes=p,feature=f)
def choose(offers,rem,force):
    eligible=[o for o in offers if o['edge']>=.01]
    fallback=not eligible and rem==1 and force
    return (eligible if eligible else offers if fallback else []),fallback
def init_db(path):
    db=sqlite3.connect(path);db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS cycles (close INTEGER PRIMARY KEY, first_seen REAL, last_seen REAL, reason TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL, kind TEXT, data TEXT)')
    db.execute('''CREATE TABLE IF NOT EXISTS trades (lane TEXT, close INTEGER, ticker TEXT, asset TEXT, decision INTEGER, entered REAL,
        side INTEGER, entry REAL, fee REAL, edge REAL, forced INTEGER, status TEXT, result TEXT, pnl REAL,
        PRIMARY KEY(lane,close))''');db.commit();return db
def event(db,kind,data):db.execute('INSERT INTO events(ts,kind,data) VALUES(?,?,?)',(time.time(),kind,json.dumps(data,separators=(',',':'))));db.commit()
def quote(c):
    try:b=float(c['yes_bid']['close_dollars']);a=float(c['yes_ask']['close_dollars'])
    except (KeyError,TypeError,ValueError):return None
    return (b,a) if 0<=b<=a<=1 else None
def completed_inputs(client,markets,T,models):
    bars={};spotraw={};out=[];diagnostics=[]
    for a in ('BTC','ETH'):bars[a],spotraw[a]=client.spot(a,T)
    for a in ('BTC','ETH'):
        m=markets.get(a)
        if not m:diagnostics.append(dict(asset=a,reason='market_missing'));continue
        raw=client.get('/series/KX'+a+'15M/markets/'+m['ticker']+'/candlesticks',dict(start_ts=T-120,end_ts=T,period_interval=1))
        cc={int(c['end_period_ts']):c for c in raw.get('candlesticks',[])};q=quote(cc.get(T,{}));prev=quote(cc.get(T-60,{}))
        diagnostics.append(dict(asset=a,ticker=m['ticker'],raw_candles=raw))
        if not q:continue
        f=features.build(T,int(timestamp(m['close_time'])),int(timestamp(m['open_time'])),q[0],q[1],prev,bars[a],bars['ETH' if a=='BTC' else 'BTC'])
        if f is None:continue
        out.append(dict(asset=a,ticker=m['ticker'],**offer(f,models[a+'_rich'])))
    return out,dict(candles=diagnostics,coinbase=spotraw)
def settle(db,client):
    pending=db.execute("SELECT DISTINCT ticker FROM trades WHERE status='open' AND close<?",(time.time()-5,)).fetchall()
    for (ticker,) in pending:
        r=client.get('/markets/'+ticker);m=r['market'];result=m.get('result')
        if result not in ('yes','no'):continue
        for lane,close,side,entry,paid in db.execute("SELECT lane,close,side,entry,fee FROM trades WHERE ticker=? AND status='open'",(ticker,)).fetchall():
            pnl=int((result=='yes')==(side==1))-entry-paid-.0025
            db.execute("UPDATE trades SET status='settled',result=?,pnl=? WHERE lane=? AND close=?",(result,pnl,lane,close))
        event(db,'settlement',{'ticker':ticker,'result':result,'source':m});db.commit()
def stats(db):
    out={}
    for lane in ('force','skip'):
        rr=db.execute('SELECT status,pnl,entry,fee,asset,forced FROM trades WHERE lane=?',(lane,)).fetchall();done=[r for r in rr if r[0]=='settled']
        net=sum(r[1] for r in done);reserve=sum(r[2]+r[3]+.0025 for r in rr if r[0]=='open')
        out[lane]=dict(entries=len(rr),settled=len(done),positive=sum(r[1]>0 for r in done),negative=sum(r[1]<0 for r in done),net=net,
            open=sum(r[0]=='open' for r in rr),virtual_cash=100+net-reserve,assets={a:sum(r[4]==a for r in rr) for a in ('BTC','ETH')},forced=sum(r[5] for r in rr))
    return out
def probe():
    c=Public();models=json.loads((P/'models.json').read_text());T=int(time.time())//60*60;mm={}
    for a in ('BTC','ETH'):
        r=c.get('/markets',dict(series_ticker='KX'+a+'15M',status='open',limit=20));active=[m for m in r.get('markets',[]) if timestamp(m['open_time'])<=time.time()<timestamp(m['close_time'])]
        if active:mm[a]=min(active,key=lambda m:m['close_time'])
    offers,raw=completed_inputs(c,mm,T,models)
    checks=[]
    for o in offers:
        b=c.get('/markets/'+o['ticker']+'/orderbook',dict(depth=0));checks.append(dict(asset=o['asset'],side=o['side'],edge=o['edge'],depth_fill=price(b,o['side']),book_keys=list(b)))
    out=dict(mode='PUBLIC_GET_ONLY_PROBE',ts=time.time(),offers=len(offers),checks=checks,raw=raw)
    atomic(P/'paper_probe.json',out);print(json.dumps({k:v for k,v in out.items() if k!='raw'},indent=2))
def run(max_days=21,max_cycles=1500):
    STATE.mkdir(exist_ok=True);db=init_db(STATE/'paper.sqlite3');client=Public();models=json.loads((P/'models.json').read_text())
    modelhash=hashlib.sha256((P/'models.json').read_bytes()).hexdigest();old=db.execute("SELECT value FROM meta WHERE key='model_hash'").fetchone()
    if old and old[0]!=modelhash:raise RuntimeError('Frozen model changed; new experiment required')
    if not old:
        db.executemany('INSERT INTO meta(key,value) VALUES(?,?)',[('model_hash',modelhash),('started',str(time.time()))]);db.commit()
    sourcehash=hashlib.sha256(b''.join((P/f).read_bytes() for f in ('paper.py','features.py','models.json'))).hexdigest()
    previous=db.execute("SELECT value FROM meta WHERE key='source_hash'").fetchone()
    if previous and previous[0]!=sourcehash:raise RuntimeError('Frozen research source changed; new experiment or explicit repair record required')
    if not previous:db.execute('INSERT INTO meta VALUES(?,?)',('source_hash',sourcehash));db.commit()
    started=float(db.execute("SELECT value FROM meta WHERE key='started'").fetchone()[0]);lastT=0;lastsettle=0;errors=0;mm={};lastclose=0;lastreason='starting'
    while not STOP:
        now=time.time();T=int(now)//60*60
        try:
            done_cycles=db.execute('SELECT COUNT(*) FROM cycles WHERE close<?',(now,)).fetchone()[0]
            database_bytes=sum(p.stat().st_size for p in STATE.glob('paper.sqlite3*'))
            if now>=started+max_days*86400 or done_cycles>=max_cycles or database_bytes>=512*1024*1024:
                atomic(STATE/'status.json',dict(mode='PAPER_ONLY',real_orders_enabled=False,state='completed',ts=now,cycles=done_cycles,database_bytes=database_bytes,stats=stats(db)));return
            if now-lastsettle>=10:settle(db,client);lastsettle=now
            if now>=lastclose:
                mm={}
                for a in ('BTC','ETH'):
                    raw=client.get('/markets',dict(series_ticker='KX'+a+'15M',status='open',limit=20));active=[m for m in raw.get('markets',[]) if timestamp(m['open_time'])<=now<timestamp(m['close_time'])]
                    if active:mm[a]=min(active,key=lambda m:m['close_time'])
                if len(mm)==2:
                    closes={int(timestamp(m['close_time'])) for m in mm.values()}
                    if len(closes)!=1:raise ValueError('BTC/ETH close mismatch')
                    lastclose=closes.pop();db.execute('INSERT OR IGNORE INTO cycles VALUES(?,?,?,?)',(lastclose,now,now,'observing'));db.commit()
                else:lastreason='market_missing'
            rem=(lastclose-T)//60
            if len(mm)==2 and T!=lastT and now-T>=2 and now-T<=25 and 1<=rem<=14:
                lastT=T;offers,raw=completed_inputs(client,mm,T,models);event(db,'decision',dict(T=T,close=lastclose,offers=offers,raw=raw))
                lastreason='below_edge' if offers else 'incomplete_features'
                execution_cache={}
                for lane in ('force','skip'):
                    if db.execute('SELECT 1 FROM trades WHERE lane=? AND close=?',(lane,lastclose)).fetchone():continue
                    candidates,fallback=choose(offers,rem,lane=='force')
                    for o in candidates:
                        if time.time()-T>35:lastreason='stale_decision';break
                        key=(o['ticker'],o['side'])
                        if key not in execution_cache:
                            before=client.get('/markets/'+o['ticker']+'/orderbook',dict(depth=0));before_t=time.time();time.sleep(1)
                            book=client.get('/markets/'+o['ticker']+'/orderbook',dict(depth=0));received=time.time();fill=price(book,o['side'])
                            execution_cache[key]=(received,fill)
                            event(db,'book_execution',dict(T=T,ticker=o['ticker'],before_ts=before_t,before=before,received_ts=received,book=book,fill=fill))
                        else:received,fill=execution_cache[key]
                        if received-T>35:lastreason='stale_decision';break
                        if fill is None:lastreason='insufficient_displayed_depth';continue
                        st=stats(db)[lane]
                        if st['open']>=2 or st['virtual_cash']<fill['price']+fill['fee']+.0025:lastreason='virtual_risk_limit';break
                        db.execute('INSERT OR IGNORE INTO trades VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (lane,lastclose,o['ticker'],o['asset'],T,received,o['side'],fill['price'],fill['fee'],o['edge'],int(fallback),'open',None,None));db.commit();lastreason='paper_entry';break
                db.execute('UPDATE cycles SET last_seen=?,reason=? WHERE close=?',(time.time(),lastreason,lastclose));db.commit()
            atomic(STATE/'status.json',dict(mode='PAPER_ONLY',real_orders_enabled=False,state='running',ts=time.time(),started=started,expires=started+max_days*86400,
                model_sha256=modelhash,source_sha256=sourcehash,cycles=done_cycles,max_cycles=max_cycles,database_bytes=database_bytes,errors=errors,last_reason=lastreason,close=lastclose,stats=stats(db)))
        except Exception as exc:
            errors+=1;lastreason=type(exc).__name__;event(db,'error',dict(type=type(exc).__name__,detail=str(exc)[:300]))
            atomic(STATE/'status.json',dict(mode='PAPER_ONLY',real_orders_enabled=False,state='retrying',ts=time.time(),errors=errors,last_reason=lastreason,stats=stats(db)))
        time.sleep(2)
def stop(*_):
    global STOP
    STOP=True
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--probe',action='store_true');a=p.parse_args()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    if a.probe:probe()
    else:run()
