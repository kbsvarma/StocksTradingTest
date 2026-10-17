"""Public GET market availability and two bounded book snapshots."""
import json,time,pathlib,requests,collections
P=pathlib.Path(__file__).resolve().parent
BASE='https://external-api.kalshi.com/trade-api/v2'
session=requests.Session();last=0
def get(path,params=None):
    global last
    time.sleep(max(0,last+1-time.monotonic()));last=time.monotonic()
    r=session.get(BASE+path,params=params,timeout=20);r.raise_for_status()
    return dict(url=r.url,received=time.time(),response=r.json())
catalog=get('/series');(P/'current_series.json').write_text(json.dumps(catalog))
counts=collections.Counter(x.get('category','unknown') for x in catalog['response'].get('series',[]))
out=dict(ts=time.time(),catalogue_categories=dict(counts),series={},books=[])
for series in ('KXDJI','KXNFLGAME','KXMLBGAME','KXNHLGAME','KXWNBAGAME','KXAUDUSD15M','KXINXU','KXNASDAQ100U','KXHIGHNY','KXFEDDECISION','KXCPI','KXHOUSECONTROL'):
    try:
        rec=get('/markets',dict(series_ticker=series,status='open',limit=1000))
        (P/(series+'_open.json')).write_text(json.dumps(rec))
        markets=rec['response'].get('markets',[])
        out['series'][series]=dict(listed=len(markets),truncated=bool(rec['response'].get('cursor')),events=len({m['event_ticker'] for m in markets}),volume_contracts=sum(float(m.get('volume_fp') or 0) for m in markets),nearest=sorted([{k:m.get(k) for k in ('ticker','title','close_time','yes_bid_dollars','yes_ask_dollars','no_bid_dollars','no_ask_dollars','volume_fp')} for m in markets],key=lambda m:m['close_time'])[:5])
    except Exception as exc:out['series'][series]=dict(error=str(exc))
dj=json.loads((P/'KXDJI_open.json').read_text())['response'].get('markets',[])
if dj:
    close=min(m['close_time'] for m in dj)
    near=[m for m in dj if m['close_time']==close]
    ranked=[]
    for m in near:
        for side in ('yes','no'):
            ask=float(m.get(side+'_ask_dollars') or 0)
            if .85<=ask<.98:ranked.append((ask,m,side))
    chosen=sorted(ranked,key=lambda r:r[0])[:2]
    for ask,m,side in chosen:
        for repeat in range(2):
            try:
                rec=get('/markets/'+m['ticker']+'/orderbook',{'depth':100})
                out['books'].append(dict(ticker=m['ticker'],side=side,reference_ask=ask,repeat=repeat,**rec))
            except Exception as exc:out['books'].append(dict(ticker=m['ticker'],error=str(exc)))
(P/'current_inventory.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({k:v for k,v in out.items() if k!='books'},indent=2))
