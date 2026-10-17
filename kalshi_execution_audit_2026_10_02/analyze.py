import json,collections,datetime,statistics
from pathlib import Path
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;r=json.loads((P/'records.json').read_text());ev=r['events'];orders=r['orders'];ET=ZoneInfo('America/New_York')
def day(t):return datetime.datetime.fromtimestamp(t,ET).date().isoformat()
# Deduplicate minute retries. Keep every distinct market and every execution reason.
dec={}
for e in ev:
 if e.get('decision_ts') and e.get('asset'):dec[(e['asset'],e['decision_ts'])]=e
actual={o['ticker']:o for o in orders};days={};unfilled=[]
for d in sorted({day(e['decision_ts']) for e in dec.values()}):
 ee=[e for e in dec.values() if day(e['decision_ts'])==d]
 price=[e for e in ee if isinstance(e.get('z'),(int,float))];z2=[e for e in price if e['z']>=2]
 tickers={e['ticker'] for e in z2};oo=[o for o in orders if day(o['created_ts'])==d]
 nofill=tickers-{o['ticker'] for o in orders if float(o.get('filled',0))>0}
 for t in sorted(nofill):
  zz=[e for e in ev if e.get('ticker')==t and isinstance(e.get('z'),(int,float)) and e['z']>=2]
  unfilled.append(dict(day=d,ticker=t,reasons=dict(collections.Counter(e.get('execution_status') or e['status'] for e in zz)),events=zz,order=actual.get(t)))
 days[d]=dict(asset_minutes=len(ee),price_qualified=len(price),z2_observations=len(z2),z2_markets=len(tickers),attempts=len(oo),filled_markets=sum(float(o.get('filled',0))>0 for o in oo),contracts=sum(float(o.get('filled',0)) for o in oo),missed_markets=len(nofill),statuses=dict(collections.Counter(e['status'] for e in ee)))
cohorts={}
for name,start,end in [('before','2026-09-23','2026-09-29'),('recent','2026-09-30','2026-10-01')]:
 cohorts[name]={}
 for asset in ['BTC','ETH','SOL','XRP']:
  a=[e for e in dec.values() if start<=day(e['decision_ts'])<=end and e['asset']==asset and isinstance(e.get('z'),(int,float))]
  if not a:continue
  cohorts[name][asset]=dict(n=len(a),z2=sum(e['z']>=2 for e in a),median_z=statistics.median(e['z'] for e in a),median_sigma_bp=10000*statistics.median(e['sigma'] for e in a),median_move_bp=10000*statistics.median(abs(e['spot']/e['strike_proxy']-1) for e in a),median_minutes=statistics.median(e['remaining_minutes'] for e in a))
res=dict(export_ts=r['export_ts'],days=days,cohorts=cohorts,missed_markets=unfilled,order_summary=dict(attempts=len(orders),filled=sum(float(o.get('filled',0))>0 for o in orders),unfilled=sum(o['status']=='unfilled' for o in orders),partial=[{k:o.get(k) for k in ['ticker','filled','requested_count']} for o in orders if 0<float(o.get('filled',0))<float(o.get('requested_count',1))]))
(P/'analysis.json').write_text(json.dumps(res,indent=2))
print('DAYS',json.dumps({d:v for d,v in days.items() if d>='2026-09-23'},indent=2));print('COHORTS',json.dumps(cohorts,indent=2));print('ORDERS',res['order_summary'])
print('MISSED RECENT',json.dumps([{k:v for k,v in x.items() if k not in ('events','order')} for x in unfilled if x['day']>='2026-09-23'],indent=2))
