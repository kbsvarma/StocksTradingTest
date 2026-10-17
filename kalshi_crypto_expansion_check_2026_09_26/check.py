import sys,json,concurrent.futures,time
from pathlib import Path
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P.parent/'kalshi_full_review_2026_09_26'));from census import get
series=get('/series')['series'];ss=[s for s in series if s.get('category')=='Crypto' and '15M' in s['ticker']]
def one(s):
 t=s['ticker'];active=get('/markets',{'series_ticker':t,'status':'open','limit':200});recent=get('/markets',{'series_ticker':t,'status':'settled','min_settled_ts':int(time.time())-86400,'limit':200});ms=recent['markets'];r={'series':t,'title':s['title'],'open':len(active['markets']),'settled_24h':len(ms),'traded_24h':sum(float(m.get('volume_fp') or m.get('volume') or 0)>0 for m in ms),'volume_contracts':sum(float(m.get('volume_fp') or m.get('volume') or 0) for m in ms),'cursor':recent.get('cursor',''),'sources':s.get('settlement_sources'), 'rules':active['markets'][0].get('rules_primary') if active['markets'] else None};return r
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:r=list(pool.map(one,ss))
(P/'current.json').write_text(json.dumps(r,indent=2))
for x in r:print(json.dumps({k:v for k,v in x.items() if k not in ['rules','sources']}))
