"""GET-only shadow validation. Never instantiates an order client or ledger writer."""
import datetime as dt,gzip,json,sys,time,statistics,hashlib,threading
from pathlib import Path
from collections import Counter,defaultdict,deque
from decimal import Decimal as D
import requests
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'candidate'))
from stream_transport import make_stream
from kalshi_client import Credentials
BASE='https://api.elections.kalshi.com/trade-api/v2'
ASSETS=('BTC','ETH','SOL','XRP','NATGAS','EURUSD','WTI')
LIVE=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
start=time.time();duration=float(sys.argv[1]) if len(sys.argv)>1 else 1000
label=sys.argv[2] if len(sys.argv)>2 else str(int(start))
raw=gzip.open(P/'receipts'/('shadow_frames_'+label+'.jsonl.gz'),'wt')
count=Counter();pairs=[];targets={};session=requests.Session();first={};errors=[]
hist=defaultdict(lambda:deque(maxlen=3000));hist_lock=threading.Lock();holder={}
def digest(b):return hashlib.sha256(json.dumps(norm(b),sort_keys=True).encode()).hexdigest()
initial={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in LIVE.glob('*.py')}
def capture(frame,wall):
    if frame.get('type') in ('orderbook_snapshot','orderbook_delta') and holder.get('ws'):
        t=frame['msg']['market_ticker'];b=holder['ws'].books.view(t,time.monotonic())
        if b is not None:
            with hist_lock:hist[t].append((wall,digest(b)))
    count[frame.get('type','unknown')]+=1
    if sum(count.values())<=80000 or frame.get('type') in ('fill','error','subscribed'):
        raw.write(json.dumps({'received_ts':wall,'frame':frame})+'\n')
def norm(book):
    return {s:sorted((str(D(p).normalize()),str(D(q).normalize())) for p,q in (book or {}).get(s,[]) if D(q)) for s in ('yes_dollars','no_dollars')}
def q(vals):
    if not vals:return {}
    s=sorted(vals)
    return {'n':len(s),'p50':s[len(s)//2],'p95':s[min(len(s)-1,int(len(s)*.95))],'max':s[-1]}
def get(path):
    r=session.get(BASE+path,timeout=2);r.raise_for_status();return r.json()
ws=make_stream(Credentials.load(),on_frame=capture);holder['ws']=ws;discovered=0;last_save=0;reconnected=False
try:
 while time.time()-start<duration:
    if time.time()-discovered>10:
        for a in ASSETS:
            try:
                now=time.time();ms=get('/markets?series_ticker=KX'+a+'15M&status=open&limit=10')['markets']
                def stamp(s):return dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
                m=next((m for m in ms if stamp(m['open_time'])<=now<stamp(m['close_time'])),None)
                if m:
                    t=m['ticker'];ws.watch(t,stamp(m['close_time']));targets[a]=t
                    first.setdefault(t,now)
            except Exception as e:errors.append({'ts':time.time(),'asset':a,'error':str(e)[:180]})
        discovered=time.time()
    for a,t in list(targets.items()):
        if time.time()-start>=duration:break
        before=ws.book(t)
        if not before:continue
        t0=time.perf_counter()
        try:rest=get('/markets/'+t+'/orderbook?depth=10')['orderbook_fp']
        except Exception as e:errors.append({'ts':time.time(),'ticker':t,'error':str(e)[:180]});continue
        rest_ms=1000*(time.perf_counter()-t0);t1=time.perf_counter();after=ws.book(t);cache_ms=1000*(time.perf_counter()-t1)
        time.sleep(.1);later=ws.book(t)
        quiet=after is not None and later is not None and (time.time()-.1-rest_ms/1000-before['_transport']['received_ts']>=.1) and after['_transport']['seq']==later['_transport']['seq']
        stable=quiet and after is not None and before['_transport']['generation']==after['_transport']['generation'] and before['_transport']['seq']==after['_transport']['seq']
        # A channel sequence may advance for another market: equal ticker levels
        # on both sides is useful separately, but not strict atomic proof.
        match=norm(rest)==norm(after) if after else None
        response_at=time.time()-.1;h=digest(rest)
        with hist_lock:aligned=any(ph==h and response_at-.5<=w<=response_at+.11 for w,ph in hist[t])
        aligned=aligned or match
        pairs.append(dict(ts=time.time(),asset=a,ticker=t,stable=stable,match=match,aligned_match=aligned,rest_ms=rest_ms,cache_ms=cache_ms,
                          before=before,rest=rest,after=after))
    if not reconnected and time.time()-start>90:
        ws.socket.close();reconnected=True;count['controlled_disconnect']+=1
    if time.time()-last_save>10:
        report=dict(start_ts=start,ts=time.time(),duration=time.time()-start,ws=ws.status(),frames=dict(count),
            assets={a:dict(comparisons=sum(x['asset']==a for x in pairs),stable=sum(x['asset']==a and x['stable'] for x in pairs),
                aligned_matches=sum(x['asset']==a and x['aligned_match'] for x in pairs),mismatches=sum(x['asset']==a and x['stable'] and not x['match'] for x in pairs)) for a in ASSETS},
            tickers=first,rest_latency_ms=q([x['rest_ms'] for x in pairs]),cache_lookup_ms=q([x['cache_ms'] for x in pairs]),
            source_lag_ms=q(list(ws.lags)),errors=errors[-40:],live_source_unchanged=initial=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in LIVE.glob('*.py')})
        tmp=P/'receipts'/('shadow_'+label+'.tmp');tmp.write_text(json.dumps(report,indent=2));tmp.replace(P/'receipts'/('shadow_'+label+'.json'))
        last_save=time.time();raw.flush()
    time.sleep(.5)
finally:
 ws.close();raw.close()
 with gzip.open(P/'receipts'/('shadow_pairs_'+label+'.jsonl.gz'),'wt') as f:
    for x in pairs:f.write(json.dumps(x)+'\n')
print(json.dumps(report,indent=2))
