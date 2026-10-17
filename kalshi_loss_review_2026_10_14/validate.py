"""Validate counterfactuals and quantify uncertainty; never changes production."""
from pathlib import Path
from collections import Counter,defaultdict
import gzip,json,pickle,math,sys
from decimal import Decimal as D
import numpy as np
import research as r
P=r.P
rows=[x for x in r.readgz(P/'orders.json.gz') if x['status']=='settled']
markets=pickle.loads((P/'recent_features.pickle').read_bytes());mm={m['ticker']:m for m in markets}
features={(m['ticker'],f['t']):f for m in markets for f in m['features']}
bridge=[];loss_ops=[]
for x in rows:
 s=x['signal'];key=(x['ticker'],s['decision_ts']);f=features.get(key)
 if f:
  assert mm[x['ticker']]['result']==(1 if x['result']=='yes' else -1)
  diffs={k:float(s[a])-float(f[b]) for k,a,b in [('z','z','z'),('spot','spot','spot'),('reference','strike_proxy','ref'),('ask','signal_ask','ask'),('bid','signal_bid','bid')]}
  bridge.append(dict(ticker=x['ticker'],lost=D(x['pnl'])<0,asset=x['asset'],diff=diffs,exact_inputs=all(abs(v)<1e-8 for v in diffs.values())))
 if D(x['pnl'])<0 and x['settled_day']>='2026-10-08':
  cap=float(x['favorite_limit']);price=float(x['entry'])
  assert price<=cap+1e-10 and D(x['filled'])==5
  assert x['created_ts']-s['decision_ts']<=35 and s['decision_ts']<x['close_ts']
  loss_ops.append(dict(ticker=x['ticker'],quantity=x['filled'],entry=price,cap=cap,price_limit_pass=True,z=s['z'],required_z=s['required_z'],minutes=s['remaining_minutes'],decision_to_submit_ms=round((x['created_ts']-s['decision_ts'])*1000,2),last_book_to_submit_ms=round((x['created_ts']-s['execution_book_ts'])*1000,2),initial_book_to_submit_ms=round((x['created_ts']-s['book_observed_ts'])*1000,2)))
ledgers=r.readgz(P/'recent_ledgers.json.gz');older=r.readgz(P/'older_crypto_ledgers.json.gz')
controls=[];validated=0
for label,periods in ledgers.items():
 stress=.02 if label=='fees2c' else .01
 for per,candidates in periods.items():
  for name,rr in candidates.items():
   assert len(rr)==len({x['ticker'] for x in rr})
   for x in rr:
    assert r.keep(x,name)
    assert x['t']<x['entered']<x['close']<=x['settled']
    assert x['quantity']==5 and .85-1e-9<=x['entry']<=x['cap']+1e-9
    assert x['won']==int(x['side']==mm[x['ticker']]['result'])
    assert abs(x['fee']-r.fee(x['entry'],mm[x['ticker']]['multiplier']))<1e-8
    assert abs(x['net']-(5*(x['won']-x['entry'])-x['fee']-5*stress))<1e-8
    validated+=1
   controls.append(dict(scenario=label,period=per,candidate=name,passed=True))
base={x['ticker']:x for x in ledgers['fees1c']['all']['baseline'] if x['asset']=='BTC'}
counterfactual_losses=[]
for x in rows:
 if x['asset']!='BTC' or D(x['pnl'])>=0 or x['ticker'] not in mm:continue
 cases={}
 for n in ['baseline','btc_max7','btc_max5','btc_z225','btc_z250','btc_z300','btc_time_z']:
  y=next((y for y in ledgers['fees1c']['all'][n] if y['ticker']==x['ticker']),None)
  cases[n]={k:y[k] for k in ('lane','side','rem','z','entry','net','won')} if y else None
 counterfactual_losses.append(dict(ticker=x['ticker'],actual_pnl=x['pnl'],actual_z=x['signal']['z'],actual_minutes=x['signal']['remaining_minutes'],replay=cases))
def bootstrap(rr,base,lo,hi,seed=20261014):
 # Paired day blocks retain same-day clustering; this is exploratory,
 # unadjusted for selecting among the candidate grid, not a proof of an edge.
 days=np.arange(lo,hi+1,86400);dd=[r.day(float(t)) for t in days]
 def daily(xs):
  a=defaultdict(float)
  for x in xs:
   if x['asset']=='BTC':a[r.day(x['settled'])]+=x['net']
  return np.array([a[d] for d in dd])
 a,b=daily(rr),daily(base);rng=np.random.default_rng(seed);idx=rng.integers(0,len(a),size=(20000,len(a)))
 net=a[idx].sum(1);diff=(a-b)[idx].sum(1)
 return dict(calendar_days=len(a),net_95pct_interval=[round(float(v),4) for v in np.quantile(net,[.025,.975])],paired_change_95pct_interval=[round(float(v),4) for v in np.quantile(diff,[.025,.975])],method='20000 paired calendar-day resamples, exploratory/unadjusted for candidate search')
unc={}
for n in ['baseline','btc_max7','btc_z250','btc_z300','btc_time_z']:
 unc[n]=bootstrap(ledgers['fees1c']['all'][n],ledgers['fees1c']['all']['baseline'],r.ts('2026-10-02T12:00:00-04:00'),r.ts('2026-10-14T12:00:00-04:00'))
 # Win-rate interval avoids the misleading zero-loss bootstrap on 21 wins.
 rr=[x for x in ledgers['fees1c']['all'][n] if x['asset']=='BTC'];count=len(rr);p=sum(x['won'] for x in rr)/count;z=1.95996398454;den=1+z*z/count
 center=(p+z*z/(2*count))/den;half=z*math.sqrt(p*(1-p)/count+z*z/(4*count*count))/den
 unc[n]['win_rate_wilson95']=[center-half,center+half]
 unc[n]['average_all_in_cost_with_friction']=sum(x['entry']+(x['fee']+.05)/5 for x in rr)/count
 unc[n]['warning']='Calendar-day bootstrap cannot invent unseen tail losses; Wilson assumes independent trades and is optimistic with clustering. Selection bias is not adjusted.'
r.save('validation.json',dict(passed=True,scenario_checks=len(controls),rows_checked=validated,actual_crypto_input_comparisons=len(bridge),exact_input_matches=sum(x['exact_inputs'] for x in bridge),input_differences=[x for x in bridge if not x['exact_inputs']],loss_execution_checks=loss_ops,exchange_losses_verified=len(json.loads((P/'exchange_loss_proof.json').read_text())),older_reproduction_controls=json.loads((P/'older_crypto_controls.json').read_text())))
r.save('actual_replay_bridge.json',bridge);r.save('loss_reentry_cases.json',counterfactual_losses);r.save('uncertainty.json',unc)
print('VALIDATED',validated,'rows,',len(bridge),'live input comparisons,',sum(x['exact_inputs'] for x in bridge),'exact inputs')
print('BTC real losses vs counterfactuals')
for c in counterfactual_losses:print(c['ticker'],{k:(round(v['net'],4),v['rem'],round(v['z'],3)) if v else None for k,v in c['replay'].items()})
print('UNCERTAINTY',json.dumps(unc))
