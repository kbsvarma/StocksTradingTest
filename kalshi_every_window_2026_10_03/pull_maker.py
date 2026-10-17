"""Public trade tape, bounded deterministic market sample, resumable."""
import argparse, concurrent.futures as cf, datetime as dt, gzip, hashlib, json, pickle, threading, time, urllib.parse, urllib.request
import requests
import screen as s
P=s.P;D=P/'maker_tapes';D.mkdir(exist_ok=True);LOCAL=threading.local()
def sample(full=False):
    mm=pickle.loads((P/'prepared.pickle').read_bytes());dd={}
    for m in mm:
        if m['asset']=='BTC' and 12 in m['new_features']:dd.setdefault(s.day(m['close']),[]).append(m)
    chosen=[]
    for day,ms in sorted(dd.items()):
        chosen.extend(sorted(ms,key=lambda m:hashlib.sha256(m['ticker'].encode()).hexdigest())[:len(ms) if full else 10])
    spec=[dict(ticker=m['ticker'],start=m['close']-720,end=m['close']-720+62,cohort=m['cohort']) for m in chosen]
    name='maker_full_sample.json' if full else 'maker_sample.json';p=P/name
    if p.exists():assert json.loads(p.read_text())==spec
    else:s.save(name,spec)
    return spec
def get(params):
    url='https://external-api.kalshi.com/trade-api/v2/markets/trades?'+urllib.parse.urlencode(params)
    for i in range(6):
        try:
            if not hasattr(LOCAL,'session'):LOCAL.session=requests.Session()
            h=LOCAL.session.get(url,timeout=25);h.raise_for_status();r=h.json()
            time.sleep(.10);return r
        except Exception:
            if i==5:raise
            time.sleep(min(8,2**i))
def pull(w):
    path=D/(w['ticker']+'.json.gz')
    if path.exists():return w['ticker'],0
    params=dict(ticker=w['ticker'],min_ts=w['start'],max_ts=w['end'],limit=1000,is_block_trade='false');pages=[];ids=set()
    while True:
        r=get(params);pages.append(r)
        for t in r.get('trades',[]):
            assert t['trade_id'] not in ids;ids.add(t['trade_id'])
        cursor=r.get('cursor')
        if not cursor:break
        params['cursor']=cursor
        if len(pages)>100:raise RuntimeError('Per-window page bound exceeded')
    with gzip.open(path.with_suffix('.tmp'),'wt') as h:json.dump(dict(window=w,pages=pages),h)
    path.with_suffix('.tmp').replace(path)
    return w['ticker'],len(ids)
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--full',action='store_true');args=ap.parse_args()
    spec=sample(args.full);print('Frozen sample',len(spec),flush=True);total=0
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for i,(ticker,n) in enumerate(pool.map(pull,spec),1):
            total+=n
            if i%25==0 or i==len(spec):print(i,len(spec),total,flush=True)
    s.save('maker_full_pull_receipt.json' if args.full else 'maker_pull_receipt.json',dict(windows=len(spec),new_prints=total,files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in D.glob('*.gz')}))
