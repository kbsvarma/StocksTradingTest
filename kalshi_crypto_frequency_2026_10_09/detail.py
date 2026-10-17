"""Join recorded qualifying signals to exact observed depth; no hypothetical fills."""
import gzip,json,collections,statistics,sys,math
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal as D
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P/'source'))
from price_rules import limit_price
from lanes import lane_for
ET=ZoneInfo('America/New_York');C=('BTC','ETH','SOL','XRP')
def read(n):
 with gzip.open(P/n,'rt') as f:return json.load(f)
def save(n,x):(P/n).write_text(json.dumps(x,indent=2)+'\n')
def day(t):return datetime.fromtimestamp(t,ET).date().isoformat()
rows=read('orders.json.gz');events=read('crypto_events_oct3_on.json.gz');obs=read('observer.json.gz');markets={r['ticker']:r for rr in read('markets.json.gz').values() for r in rr}
first={};bykey=collections.defaultdict(list)
for e in events:
 k=(e.get('lane'),e.get('ticker'),e['decision_ts']);bykey[k].append(e);first.setdefault(k,e)
qualified={k:v for k,v in first.items() if v.get('z',-999)>= (2 if k[0]=='cushion_z2' else 3) and 3<=v.get('remaining_minutes',0)<=10}
filled={r['ticker'] for r in rows if float(r.get('filled',0))>0};missing=json.loads((P/'qualified_unfilled.json').read_text())
# Avoid falsely joining earlier near-threshold observer samples: exact lane/ticker/decision key AND signal passes real gates.
joined=collections.defaultdict(list);invalid_observer=0
for ob in obs:
 key=(ob.get('lane','cushion_z2'),ob.get('ticker'),ob.get('decision_ts'))
 if key not in qualified:
  invalid_observer+=1;continue
 s=qualified[key];rule=lane_for(s);cap=limit_price(s)[1];qty=D(0);cost=D(0);weak=False
 for px,n in sorted((D(1)-D(px),D(n)) for px,n in ob.get('levels',[])):
  if n<=0:continue
  if px<rule.floor:weak=True;break
  if px>cap:break
  take=min(D(5)-qty,n);qty+=take;cost+=take*px
  if qty==5:break
 age=ob.get('observed_ts',0)-s['decision_ts']
 if qty==5 and not weak and 0<=age<=35:
  # Terminal weakened-below-floor event occurred BEFORE this observed recovery?
  weakened=[e for e in bykey[key] if e.get('_saved_ts',0)<ob['observed_ts'] and (e.get('observed_ask',1)<float(rule.floor) or e.get('execution_status')=='Book weakened below floor')]
  joined[ob['ticker']].append(dict(lane=key[0],decision_ts=key[2],age=age,vwap=float(cost/5),cap=float(cap),before25=age<=25,after_floor_terminal=bool(weakened),observed_ts=ob['observed_ts']))
miss_summary={}
for t,rr in sorted(missing.items()):
 reasons=collections.Counter(e.get('execution_status') or e['status'] for e in rr)
 reason='risk' if 'Daily loss budget including open risk' in reasons else 'zero_fill' if any('zero' in x.lower() or 'Canceled' in x for x in reasons) else 'floor' if any(e.get('observed_ask',1)<float(lane_for(e).floor) or e.get('execution_status')=='Book weakened below floor' for e in rr) else 'price_or_depth'
 miss_summary[t]=dict(day=day(rr[0]['decision_ts']),reason=reason,observed_reasons=dict(reasons),qualified_observer_good_books=joined.get(t,[]),side=rr[0]['side'],result=markets.get(t,{}).get('result'))
save('missed_classification.json',miss_summary)
byday={}
for d in sorted(set(day(e['decision_ts']) for e in events)):
 v={}
 for a in C:
  ff=[e for e in first.values() if e['asset']==a and e['lane']=='cushion_z2' and day(e['decision_ts'])==d]
  features=[e for e in ff if 'sigma' in e];sig=[e for e in features if e['z']>=2]
  v[a]=dict(price_qualified_minutes=len(features),z_qualified_minutes=len(sig),median_sigma=statistics.median(e['sigma'] for e in features) if features else None,median_z=statistics.median(e['z'] for e in features) if features else None)
 byday[d]=v
save('volatility_by_asset.json',byday)
# Quantify actual benefited retries rather than claiming every book check is another trade.
retry={}
for d in sorted(set(day(r['created_ts']) for r in rows)):
 if d<'2026-10-03':continue
 rr=[r for r in rows if day(r['created_ts'])==d and r['asset'] in C and float(r.get('filled',0))>0]
 retry[d]=dict(entries=len(rr),second_or_later_book_check=sum(r.get('signal',{}).get('execution_check_number',1)>1 for r in rr),after_eight_book_checks=sum(r.get('signal',{}).get('execution_check_number',1)>8 for r in rr),second_or_later_ioc=sum(r.get('attempt_number',1)>1 for r in rr),partial=sum(float(r['filled'])<float(r.get('requested_count',1)) for r in rr),median_entry_age=statistics.median(r['created_ts']-r['signal']['decision_ts'] for r in rr) if rr else None)
save('retry_results.json',retry)
print('MISSES_BY_DAY',json.dumps({d:dict(collections.Counter(r['reason'] for r in miss_summary.values() if r['day']==d)) for d in byday},indent=2))
print('VALID_GOOD_BOOKS_MISSES',json.dumps({t:x for t,x in miss_summary.items() if x['qualified_observer_good_books'] and x['day']>='2026-10-05'},indent=2))
print('RETRIES',json.dumps(retry,indent=2));print('VOL',json.dumps(byday,indent=2))
