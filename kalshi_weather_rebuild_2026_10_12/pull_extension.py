"""Bounded public GET extension; yields each minute's first 35s to live bots."""
from pathlib import Path
from collections import defaultdict
import sys,time,hashlib,json,gzip,requests
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P.parent/'kalshi_broad_review_2026_10_10'))
from common import read,save,ts
BASE='https://external-api.kalshi.com/trade-api/v2'
START=ts('2026-10-10T04:00:00Z');END=ts('2026-10-12T04:00:00Z')
CITIES={'KXTEMPMIAH':'miami','KXTEMPCHIHS':'chicago','KXTEMPLAXHS':'la-coastal','KXTEMPNYCHS':'nyc'}
session=requests.Session();last=0;count=0
def get(path,params=None):
    global last,count
    assert path.startswith(('/markets','/series','/live_data/weather/'))
    url=requests.Request('GET',BASE+path,params=params).prepare().url
    dest=P/'extension_raw'/(hashlib.sha256(url.encode()).hexdigest()+'.json.gz')
    if dest.exists():return read(dest)['response']
    assert count<150
    for retry in range(3):
        now=time.time();sec=now%60
        if sec<36:time.sleep(36-sec)
        elif sec>56:time.sleep(60-sec+36)
        time.sleep(max(0,last+1.5-time.time()));last=time.time();count+=1
        r=session.get(url,timeout=12)
        if r.status_code==429:
            save(P/'extension_rate_limit.json',{'ts':time.time(),'url':url,'count':count})
            raise RuntimeError('429; stopping public research collection to protect trading')
        try:r.raise_for_status();x=r.json()
        except (requests.RequestException,ValueError):
            if retry==2:raise
            time.sleep(3);continue
        save(dest,{'url':url,'retrieved_ts':time.time(),'response':x});return x
for series,city in CITIES.items():
    d=P/'extension'/series;ms=[];cursor=''
    while True:
        obj=get('/markets',{'series_ticker':series,'min_close_ts':START,'max_close_ts':END,'limit':1000,**({'cursor':cursor} if cursor else {})})
        ms+=obj['markets'];cursor=obj.get('cursor')
        if not cursor:break
    save(d/'markets.json',ms);groups=defaultdict(list)
    for m in ms:groups[m['event_ticker']].append(m)
    bars={}
    for lo in range(START,END,86400):
        x=get('/live_data/weather/'+city,{'from':(lo-7200)*1000,'to':min(lo+86400,END)*1000})
        for a in x['timeseries']:
            if a.get('v') is None:continue
            t=int(a['t'])//1000;v=(float(a['v']),a.get('status'))
            if t in bars:assert bars[t]==v
            bars[t]=v
    save(d/'bars.json.gz',bars)
    evs=sorted(groups,key=lambda e:groups[e][0]['close_time'])
    for i in range(0,len(evs),4):
        es=evs[i:i+4];items=[m for e in es for m in groups[e]]
        lo=min(ts(m['close_time']) for m in items)-41*60;hi=max(ts(m['close_time']) for m in items)
        step=(8000//len(items)-2)*60;cm=defaultdict(list)
        for start in range(lo,hi,step):
            x=get('/markets/candlesticks',{'market_tickers':','.join(m['ticker'] for m in items),'start_ts':start,'end_ts':min(start+step,hi),'period_interval':1})
            for m in x['markets']:cm[m['market_ticker']]+=m['candlesticks']
        for e in es:
            candles={}
            for m in groups[e]:
                close=ts(m['close_time']);cs={c['end_period_ts']:c for c in cm[m['ticker']] if close-41*60<=c['end_period_ts']<=close}
                candles[m['ticker']]=[cs[t] for t in sorted(cs)]
            save(d/(e+'_candles.json.gz'),candles)
    # Current source diagnostics and availability; no book polling or orders.
    detail=get('/live_data/weather/'+city,{'last_sec':3600,'detailed':'true'})
    save(d/'current_detailed.json',detail)
    cal=get('/live_data/weather/'+city+'/calibrations')
    save(d/'calibrations.json',cal)
    open_markets=get('/markets',{'series_ticker':series,'status':'open','limit':1000})
    save(d/'current_markets.json',open_markets)
    save(d/'receipt.json',{'start':START,'end':END,'events':len(groups),'markets':len(ms),'source_minutes':len(bars),'completed_ts':time.time()})
    print(series,len(groups),len(bars),'requests',count,flush=True)
save(P/'extension_receipt.json',{'completed_ts':time.time(),'requests':count,'mutating_requests':0})
