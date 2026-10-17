"""Read-only FX trading-volume and current-book check. No order client."""
import collections as C,datetime as dt,gzip,hashlib,json,math,pickle,statistics,time
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
P=Path(__file__).resolve().parent
OLD=P.parent/'kalshi_currency_review_2026_10_12'
ET=ZoneInfo('America/New_York')
BASE='https://external-api.kalshi.com/trade-api/v2'
session=requests.Session();calls=[]
def get(path,params=None):
    start=time.time();r=session.get(BASE+path,params=params,timeout=8);r.raise_for_status()
    calls.append(dict(url=r.url,received_ts=time.time(),elapsed=time.time()-start))
    time.sleep(.4)
    return r.json()
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def ts(x):return dt.datetime.fromisoformat(x.replace('Z','+00:00')).timestamp()
def summary(xs):
    xs=sorted(xs);n=len(xs)
    return dict(n=n,total=round(sum(xs),4),mean=statistics.mean(xs),median=statistics.median(xs),
        p10=xs[max(0,math.ceil(.1*n)-1)],p90=xs[max(0,math.ceil(.9*n)-1)],minimum=min(xs),maximum=max(xs),
        zero=sum(x==0 for x in xs),below5=sum(x<5 for x in xs),below100=sum(x<100 for x in xs)) if xs else dict(n=0)
def booksides(book):
    out={}
    for side,opposite in [('yes','no'),('no','yes')]:
        asks=sorted((1-D(p),D(q)) for p,q in book.get(opposite+'_dollars',[]) if D(q)>0)
        bids=sorted(((D(p),D(q)) for p,q in book.get(side+'_dollars',[]) if D(q)>0),reverse=True)
        ask=float(asks[0][0]) if asks else None;bid=float(bids[0][0]) if bids else None
        quantity=D(0);cost=D(0)
        for px,q in asks:
            take=min(5-quantity,q);quantity+=take;cost+=take*px
            if quantity==5:break
        out[side]=dict(bid=bid,ask=ask,spread=None if ask is None or bid is None else round(ask-bid,6),
            best_ask_quantity=float(asks[0][1]) if asks else 0,five_contract_vwap=float(cost/quantity) if quantity==5 else None,
            depth_at_or_below_93c=float(sum(q for px,q in asks if px<=D('.93'))),
            depth_at_or_below_97c=float(sum(q for px,q in asks if px<=D('.97'))))
    return out
assets=('AUDUSD','USDCAD','EURUSD')
raw={a:json.loads((OLD/'data'/f'markets_{a}.json').read_text()) for a in assets}
common=set.intersection(*({ts(r['close_time']) for r in raw[a]} for a in assets))
historical={}
features=pickle.loads((OLD/'features.pickle').read_bytes());fm={m['ticker']:m for m in features}
with gzip.open(OLD/'ledgers.json.gz','rt') as f:ledgers=json.load(f)
for a in assets:
    rows=[r for r in raw[a] if ts(r['close_time']) in common];daily=C.defaultdict(list)
    for r in rows:daily[dt.datetime.fromtimestamp(ts(r['close_time'])-1,ET).date().isoformat()].append(float(r['volume_fp']))
    eligible=[f for m in features if m['asset']==a and m['close'] in common for f in m['features'] if 1<=f['rem']<=10]
    band=[f for f in eligible if .85-1e-8<=f['ask']<.98-1e-8]
    chosen=[r for r in ledgers['eurusd_adaptive']['quote1c'][a]['full'] if r['close'] in common]
    prior5=[next(f['vol5'] for f in fm[r['ticker']]['features'] if f['t']==r['t']) for r in chosen]
    historical[a]=dict(window_volume=summary([float(r['volume_fp']) for r in rows]),
        daily={k:summary(v) for k,v in sorted(daily.items())},
        entry_band_minutes=len(band),entry_band_spread_median=statistics.median(f['ask']-f['bid'] for f in band),
        entry_band_spread_le2c=sum(f['ask']-f['bid']<=.02000001 for f in band),
        modeled_entries=len(chosen),volume_in5min_before_modeled_entry=summary(prior5),
        modeled_entry_total_window_volume=summary([fm[r['ticker']]['volume'] for r in chosen]))
current={};receipts={}
for a in assets:
    listing=get('/markets',dict(series_ticker='KX'+a+'15M',status='open',limit=20))
    now=time.time();m=next(x for x in listing['markets'] if ts(x['open_time'])<=now<ts(x['close_time']))
    b=get('/markets/'+m['ticker']+'/orderbook');book=b.get('orderbook_fp',{})
    current[a]=dict(ticker=m['ticker'],snapshot_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
        close=m['close_time'],elapsed_minutes=(now-ts(m['open_time']))/60,
        volume_so_far=float(m['volume_fp']),open_interest=float(m['open_interest_fp']),sides=booksides(book))
    receipts[a]=dict(markets=listing,book=b)
save('raw_current.json',dict(calls=calls,assets=receipts))
result=dict(checked_utc=dt.datetime.now(dt.timezone.utc).isoformat(),common_windows=len(common),
    first_close=min(common),last_close=max(common),historical=historical,current=current,
    caveat='Volume is contracts traded, not dollar turnover or standing order depth. End-window volume includes trades after hypothetical entry. Minute volume before entry and current book are separate diagnostics. No order sent; current snapshots are not qualifying-signal fill proof.')
save('results.json',result)
print(json.dumps(result,indent=2));session.close()
