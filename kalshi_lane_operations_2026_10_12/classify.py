"""Inspect saved receipts only; do not infer fills from isolated book snapshots."""
import json,gzip,sys,collections
from pathlib import Path
from decimal import Decimal as D
from datetime import datetime
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P/'source'))
from lanes import lane_for
from price_rules import limit_price
ET=ZoneInfo('America/New_York')
def load(n):return json.load(gzip.open(P/(n+'.json.gz'),'rt'))
def save(n,x):(P/n).write_text(json.dumps(x,indent=2)+'\n')
def et(t):return datetime.fromtimestamp(t,ET).isoformat()
miss=json.loads((P/'qualified_unfilled.json').read_text());obs=load('observer');events=load('events')
bykey=collections.defaultdict(list)
for e in events:bykey[e['asset'],e['lane'],e['decision_ts']].append(e)
def depth(levels,cap,floor):
 qty=D(0);cost=D(0);weak=False;best=None;worst=None
 for px,n in sorted((1-D(str(p)),D(str(n))) for p,n in levels):
  if n<=0:continue
  if best is None:best=px
  if px<floor:weak=True;break
  if px>cap:break
  take=min(5-qty,n);qty+=take;cost+=take*px;worst=px
  if qty==5:break
 return dict(full=qty==5 and not weak,depth=str(qty),weak=weak,best=str(best),worst=str(worst),average=str(cost/qty) if qty else None)
result={}
for ticker,es in miss.items():
 keys={(e['lane'],e['decision_ts']) for e in es};snapshots=[];seen=set()
 for e in es:
  for field,ts in [('book_levels','book_observed_ts'),('execution_book_levels','execution_book_ts')]:
   if field not in e:continue
   ident=(e['lane'],e['decision_ts'],field,e[ts])
   if ident in seen:continue
   seen.add(ident);cap=limit_price(e)[1];floor=lane_for(e).floor;age=e[ts]-e['decision_ts']
   d=depth(e[field],cap,floor)
   snapshots.append(dict(source=field,lane=e['lane'],decision_ts=e['decision_ts'],ts=e[ts],et=et(e[ts]),age=age,within_retry=0<=age<=25,cap=str(cap),execution=e.get('execution_status'),status=e['status'],**d))
 for o in obs:
  if o.get('ticker')!=ticker or (o.get('lane'),o.get('decision_ts')) not in keys or 'levels' not in o:continue
  e=next(e for e in es if e['lane']==o['lane'] and e['decision_ts']==o['decision_ts']);age=o['observed_ts']-e['decision_ts'];cap=limit_price(e)[1]
  snapshots.append(dict(source='independent_observer',lane=e['lane'],decision_ts=e['decision_ts'],ts=o['observed_ts'],et=et(o['observed_ts']),age=age,within_retry=0<=age<=25,cap=str(cap),**depth(o['levels'],cap,lane_for(e).floor)))
 result[ticker]=dict(asset=es[0]['asset'],first_et=et(es[0]['decision_ts']),qualified_minutes=len(keys),reasons=dict(collections.Counter(e.get('execution_status') or e['status'] for e in es)),snapshots=len(snapshots),valid_within_retry=[s for s in snapshots if s['full'] and s['within_retry']],valid_after_retry=[s for s in snapshots if s['full'] and not s['within_retry']],weak_count=sum(s['weak'] for s in snapshots),all_snapshots=snapshots)
save('miss_classification.json',result)
for t,r in result.items():print(t,'snapshots',r['snapshots'],'good<=25',len(r['valid_within_retry']),'good>25',len(r['valid_after_retry']),'weak',r['weak_count']);print(json.dumps(r['valid_within_retry']))
start=int(datetime(2026,10,11,22,35,tzinfo=ET).timestamp());end=int(json.loads((P/'summary.json').read_text())['ts']//60)*60
bad=[]
for (asset,lane,ts),es in bykey.items():
 if not start<=ts<end:continue
 if any(e['status'] in ('data_error','incomplete_spot','decision_candle_unavailable') for e in es):
  bad.append(dict(asset=asset,lane=lane,ts=ts,et=et(ts),first=es[0]['status'],last=es[-1]['status'],elapsed=es[-1]['_saved_ts']-ts,reasons=list(set(e.get('reason',e.get('error','')) for e in es)),trace=es))
save('input_failures.json',bad)
print('INPUT',collections.Counter((x['first'],x['last']) for x in bad))
