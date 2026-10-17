"""Audit the all-seven-lane extension and losing-market replacement signals."""
import research as r
import pickle,json,gzip
from decimal import Decimal as D
P=r.P
ms=pickle.loads((P/'noncrypto_features.pickle').read_bytes())+pickle.loads((P/'recent_features.pickle').read_bytes())
mm={m['ticker']:m for m in ms};ff={(m['ticker'],f['t']):f for m in ms for f in m['features']}
ll=r.readgz(P/'all_recent_ledgers.json.gz');validated=0
for scenario,candidates in ll.items():
 stress=.02 if scenario=='fees2c' else .01
 for n,rr in candidates.items():
  assert len(rr)==len({x['ticker'] for x in rr})
  for x in rr:
   assert r.keep(x,n) and x['quantity']==5 and x['t']<x['entered']<x['close']<=x['settled']
   rule=r.lane_for(x)
   assert float(rule.floor)-1e-8<=x['entry']<=x['cap']+1e-8
   assert x['z']+1e-9>=rule.threshold(x['rem']) and rule.min_minutes<=x['rem']<=rule.max_minutes
   assert x['won']==int(x['side']==mm[x['ticker']]['result'])
   assert abs(x['fee']-r.fee(x['entry'],mm[x['ticker']]['multiplier']))<1e-8
   assert abs(x['net']-(5*(x['won']-x['entry'])-x['fee']-5*stress))<1e-8
   validated+=1
bridge=[];paths=[]
for x in r.readgz(P/'orders.json.gz'):
 if x['asset'] in r.CRYPTO or x['status']!='settled' or x['ticker'] not in mm:continue
 s=x['signal'];f=ff.get((x['ticker'],s['decision_ts']))
 assert mm[x['ticker']]['result']==(1 if x['result']=='yes' else -1)
 if f:
  diffs={k:float(s[a])-float(f[b]) for k,a,b in [('z','z','z'),('spot','spot','spot'),('reference','strike_proxy','ref'),('ask','signal_ask','ask'),('bid','signal_bid','bid')]}
  bridge.append(dict(ticker=x['ticker'],lost=D(x['pnl'])<0,diff=diffs,exact=all(abs(v)<1e-8 for v in diffs.values())))
 if D(x['pnl'])<0:
  paths.append(dict(ticker=x['ticker'],actual_pnl=x['pnl'],replay={n:next(({k:y[k] for k in ('lane','side','rem','z','net','entry','won')} for y in rr if y['ticker']==x['ticker']),None) for n,rr in ll['fees1c'].items() if n=='baseline' or n.startswith(x['asset'].lower())}))
r.save('noncrypto_validation.json',dict(passed=True,rows_checked=validated,live_inputs_checked=len(bridge),exact_matches=sum(x['exact'] for x in bridge),differences=[x for x in bridge if not x['exact']],loss_reentry_cases=paths))
print('Validated',validated,'rows. Inputs:',len(bridge), 'exact',sum(x['exact'] for x in bridge))
for p in paths:print(json.dumps(p))
