"""Independent arithmetic, underlying-feature and uncertainty audit."""
import collections as C
import datetime as dt
import gzip,hashlib,json,math,pickle,statistics
from decimal import Decimal as D,ROUND_CEILING
from pathlib import Path
import numpy as np
import replay as r
P=r.P;ROOT=r.ROOT
def read(n):return json.loads((P/n).read_text())
manifest=read('input_manifest.json');checks=C.Counter()
for v in manifest.values():assert hashlib.sha256(Path(v['path']).read_bytes()).hexdigest()==v['sha256'];checks['input_hashes']+=1
oldbars=pickle.loads((ROOT/'kalshi_frequency_expansion_2026_10_12/financial_inputs/bars.pickle').read_bytes())['WTI']
fxbars=pickle.loads((P/'inputs/bars.pickle').read_bytes())['EURUSD']
newbars={};revisions=0
for p in sorted((ROOT/'kalshi_loss_review_2026_10_14/noncrypto_data/charts').glob('*WTI*.json.gz')):
 with gzip.open(p,'rt') as f:x=json.load(f)['response']['live_data']['details']
 for point in x['timeseries']:
  t=int(point['t']);v=float(point['v'])
  if t%60000!=59000:continue
  t=t//60000*60
  assert t not in newbars or newbars[t]==v
  newbars[t]=v
sources=[('wti_prior',oldbars,lambda m:m['asset']=='WTI' and r.START<=m['close']<r.SPLIT),('fx',fxbars,lambda m:m['asset']=='EURUSD' and r.START<=m['close']<r.END),('recent_noncrypto',newbars,lambda m:m['asset']=='WTI' and r.SPLIT<=m['close']<r.END)]
feature_checks=C.Counter();max_error=0
for label,bars,keep in sources:
 ms=pickle.loads(Path(manifest[label]['path']).read_bytes())
 for m in filter(keep,ms):
  for f in m['features']:
   if not (3 if m['asset']=='WTI' else 1)<=f['rem']<=10:continue
   t=int(f['t']);values=[bars[k] for k in range(t-3660,t,60)];ref=bars[int(m['start'])-60]
   sigma=statistics.pstdev(math.log(y/x) for x,y in zip(values,values[1:]));z=f['side']*(values[-1]/ref-1)/(sigma*math.sqrt(f['rem']))
   error=abs(z-f['z']);max_error=max(max_error,error)
   assert error<1e-7 and f['spot']==values[-1] and f['ref']==ref,(label,m['ticker'],t,error)
   assert f['rem']==(m['close']-t)/60 and t-1<t
   pair=r.quote(m['candles'][t],'c',f['side']);assert abs(pair[0]-f['bid'])<1e-8 and abs(pair[1]-f['ask'])<1e-8
   feature_checks[label]+=1
with gzip.open(P/'ledgers.json.gz','rt') as f:ledgers=json.load(f)
results=read('results.json');uncertainty={};frequency={}
coverage=read('coverage.json')
for group,configs in ledgers.items():
 for name,scenarios in configs.items():
  for scenario,periods in scenarios.items():
   for period,rows in periods.items():
    total=D(0);fees=D(0)
    for x in rows:
     price=D(str(x['entry']));raw=(D('.07')*D(str(x['multiplier']))*5*price*(1-price)).quantize(D('.000001'),rounding=ROUND_CEILING)
     expected=(5*price+raw).quantize(D('.0001'),rounding=ROUND_CEILING)-5*price
     assert abs(float(expected)-x['fee'])<1e-8
     amount=5*(D(x['won'])-price)-expected-D(str(x['friction']));total+=amount;fees+=expected
     assert abs(float(amount)-x['net'])<1e-8
     checks['decimal_ledger_rows']+=1
    assert abs(float(total)-results[group][name][scenario][period]['net'])<.000051
    base=ledgers[group]['current'][scenario][period];diff=results[group][name][scenario][period]['comparison']
    b={x['ticker']:x for x in base};n={x['ticker']:x for x in rows};common=set(b)&set(n)
    parts=sum(x['net'] for k,x in n.items() if k not in b)-sum(x['net'] for k,x in b.items() if k not in n)+sum(n[k]['net']-b[k]['net'] for k in common)
    assert abs(parts-diff['net_change'])<.000051
    checks['delta_decompositions']+=1
for asset in ('WTI','EURUSD'):
 start=max(r.START,coverage[asset][asset]['first_open']);end=r.END-1
 days=[];d=dt.datetime.fromtimestamp(start,r.ET).date();last=dt.datetime.fromtimestamp(end,r.ET).date()
 while d<=last:days.append(str(d));d+=dt.timedelta(days=1)
 full_days=days[1:-1];weekday_days=[d for d in full_days if dt.date.fromisoformat(d).weekday()<5]
 elapsed=(end-start)/86400;uncertainty[asset]={};frequency[asset]={}
 base=results[asset]['current']['fees1c']['full'];base_daily=base['daily'];rng=np.random.default_rng(20261014);indices=rng.integers(0,len(days),size=(20000,len(days)))
 for name in r.CONFIGS:
  q=results[asset][name]['fees1c']['full'];counts=q['daily_trades'];dd=np.array([q['daily'].get(d,0)-base_daily.get(d,0) for d in days]);boot=dd[indices].sum(1)
  uncertainty[asset][name]=dict(incremental_net_day_bootstrap95=list(map(float,np.quantile(boot,[.025,.975]))),method='20,000 independent calendar-day block resamples with zero-trade dates; exploratory only, no correction for repeated strategy selection or longer serial dependence')
  frequency[asset][name]=dict(calendar_elapsed_days=elapsed,total_per_calendar_day=q['trades']/elapsed,extra_per_calendar_day=q['comparison']['extra_trades']/elapsed,complete_weekdays=len(weekday_days),total_per_complete_weekday=sum(counts.get(d,0) for d in weekday_days)/len(weekday_days),extra_per_complete_weekday=sum(counts.get(d,0)-base['daily_trades'].get(d,0) for d in weekday_days)/len(weekday_days))
r.save('independent_audit.json',dict(passed=True,checks=dict(checks),feature_checks=dict(feature_checks),max_z_recomputation_error=max_error,source_before_signal='All 61 underlying minute endpoints end no later than decision timestamp minus one second; no interpolated bars.',uncertainty=uncertainty,frequency=frequency))
print('AUDIT PASSED',dict(checks),dict(feature_checks),'max_z_error',max_error)
for a in frequency:
 print(a,{k:dict(extra_per_calendar_day=round(v['extra_per_calendar_day'],3),extra_per_complete_weekday=round(v['extra_per_complete_weekday'],3),net_delta_CI=uncertainty[a][k]['incremental_net_day_bootstrap95']) for k,v in frequency[a].items()})
