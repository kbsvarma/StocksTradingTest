"""Historical source cross-check of rejected post-deployment signals; GET only."""
import collections,json,math,statistics,sys,time
from pathlib import Path
import requests
P=Path(__file__).resolve().parent;L=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
sys.path.insert(0,str(L))
from lanes import lane_for,CRYPTO_ASSETS
events=json.loads((P/'events.json').read_text());comp=json.loads((P/'candle_comparison.json').read_text())['comparisons']
first={}
for r in events:first.setdefault((r.get('ticker'),r.get('lane'),r['decision_ts']),r)
check={}
for c in comp:
 if c['decision_ts']<1792002540:continue
 r=first[(c['ticker'],c['lane'],c['decision_ts'])];rule=lane_for(r)
 if not float(rule.floor)-1e-8<=c['final_ask']<float(rule.ceiling)-1e-8 or c['final_ask']-c['final_bid']>.02+1e-8:continue
 check[(c['ticker'],c['lane'],c['decision_ts'])]=(r,c)
S=requests.Session();out=P/'source_receipts.jsonl';assert not out.exists()
def get(url,params=None):
 time.sleep(2)
 if time.time()%60<9:time.sleep(9-time.time()%60)
 start=time.time();res=S.get(url,params=params,timeout=3);data=res.json()
 with out.open('a') as f:f.write(json.dumps(dict(url=res.url,started=start,received=time.time(),status=res.status_code,data=data))+'\n')
 res.raise_for_status();return data
bars=collections.defaultdict(dict)
assets={r['asset'] for r,c in check.values()}
for asset in sorted(assets&set(CRYPTO_ASSETS)):
 ts=[r['decision_ts'] for r,c in check.values() if r['asset']==asset]
 raw=get('https://api.exchange.coinbase.com/products/'+asset+'-USD/candles',dict(start=min(ts)-3660,end=max(ts),granularity=60))
 bars[asset].update({int(x[0]):float(x[4]) for x in raw})
targets={r['ticker'] for r,c in check.values() if r['asset'] not in CRYPTO_ASSETS}
closes={r['ticker']:(r['asset'],int(r['decision_ts']+r['remaining_minutes']*60)) for r in events if r.get('ticker') and r.get('remaining_minutes') is not None}
needed=set(targets)
for t in targets:
 a,close=closes[t];prior=[(tm,tkr) for tkr,(asset,tm) in closes.items() if asset==a and tm<close]
 if prior:needed.add(max(prior)[1])
base='https://api.elections.kalshi.com/trade-api/v2'
metadata=get(base+'/markets',dict(tickers=','.join(sorted(needed)),limit=1000))['markets'] if needed else []
for m in sorted(metadata,key=lambda m:m['close_time']):
 asset=closes[m['ticker']][0];raw=get(base+'/live_data/events/'+m['event_ticker'],dict(range='1h'))
 detail=raw['live_data']['details'];assert detail['asset']==asset
 for point in detail['timeseries']:
  t=int(point['t'])
  if t%60000==59000:bars[asset][t//60000*60]=float(point['v'])
results=[]
for r,c in check.values():
 a=r['asset'];T=r['decision_ts'];close=int(T+r['remaining_minutes']*60);start=close-900;keys=list(range(T-3660,T,60));b=bars[a]
 result=dict(asset=a,lane=r['lane'],ticker=r['ticker'],T=T,live_status=r['status'],live_z=r.get('z'),old_ask=r['signal_ask'],final_ask=c['final_ask'],final_side=c['final_side'])
 if start-60 not in b or not all(k in b for k in keys):result.update(status='missing_historical_source',missing=[k for k in keys+[start-60] if k not in b])
 else:
  vals=[b[k] for k in keys];sigma=statistics.pstdev(math.log(y/x) for x,y in zip(vals,vals[1:]));z=(1 if c['final_side']=='yes' else -1)*(vals[-1]/b[start-60]-1)/(sigma*math.sqrt(r['remaining_minutes']))
  threshold=lane_for(r).threshold(r['remaining_minutes']);result.update(final_z=z,required_z=threshold,status='qualified_in_final_data' if z>=threshold else 'cushion_gate')
 results.append(result)
(P/'source_comparison.json').write_text(json.dumps(dict(ts=time.time(),checks=results),indent=2))
print(json.dumps(dict(checked=len(results),statuses=dict(collections.Counter(x['status'] for x in results)),qualified=[x for x in results if x['status']=='qualified_in_final_data'])),flush=True)
