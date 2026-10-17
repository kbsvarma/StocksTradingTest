"""Second exploratory pass: prior completed-minute movement; no future bars."""
import json,gzip,pickle,math
from pathlib import Path
import portfolio as b
P=Path(__file__).resolve().parent
names=['baseline','crypto_not_reversing','crypto_z_not_falling','crypto_z_persistent','all_not_reversing','all_z_not_falling','all_z_persistent']
(P/'SECOND_PASS_PROTOCOL.txt').write_text('Exploratory second pass after observing threshold/correlation results. Test only three fixed predicates: underlying one-minute movement not against current bet; signed z not falling from prior minute; prior minute has same favorite side and meets current required z. Each for crypto only and all assets. Prior exact T-60 completed feature required, never forward-filled. No live change, no claim of fresh holdout.\n')
fm={};features={}
for folder in ('kalshi_btc_eth_quant_2026_10_02','kalshi_noncrypto_tuning_2026_10_07'):
 markets=pickle.loads((P.parent/folder/'features.pickle').read_bytes())
 for m in markets:
  if m['asset'] not in b.ASSETS:continue
  fm[m['ticker']]=m;features[m['ticker']]={x['t']:x for x in m['features']}
with gzip.open(P.parent/'kalshi_wti_2026_10_07/budget_opportunities.json.gz','rt') as f:pools=json.load(f)
# Exact alignment of research pools with their original feature inputs.
for x in pools['fees1c']:
 assert abs(features[x['ticker']][x['t']]['z']-x['z'])<1e-9

def allowed(x,name):
 if name=='baseline':return True
 if name.startswith('crypto_') and x['asset'] not in b.ASSETS[:4]:return True
 prev=features[x['ticker']].get(x['t']-60);curr=features[x['ticker']][x['t']]
 if prev is None:return False
 if name.endswith('not_reversing'):return x['side']*(curr['spot']-prev['spot'])>=0
 if name.endswith('not_falling'):return curr['z']>=prev['z']*prev['side']*x['side']
 if name.endswith('persistent'):
  if x['asset'] in b.ASSETS[:4]:threshold=2 if x['lane']==b.s.r.Z2 else 3
  else:
   c=b.s.CONFIG[b.RULES[x['asset']]]
   threshold=max(.5,c['z2' if x['lane']==b.s.r.Z2 else 'z3']+c['slope']*math.log2(x['rem']/5))
  return prev['side']==x['side'] and prev['z']>=threshold
 raise ValueError(name)
params=dict(budget=20,exposure=25,max_contracts=25,z3_contracts=25,z3_exposure=25)
periods={'matched10d':(b.s.r.ts('2026-09-23T00:00:00Z'),b.s.r.SPLIT,False),'first5d':(b.s.r.ts('2026-09-23T00:00:00Z'),b.s.r.ts('2026-09-28T00:00:00Z'),False),'second5d':(b.s.r.ts('2026-09-28T00:00:00Z'),b.s.r.SPLIT,False),'broader_available':(b.s.r.START,b.s.r.SPLIT,False),'later_noncrypto':(b.s.r.SPLIT,b.s.r.END,True)}
results={}
for pn,(start,end,noncrypto) in periods.items():
 results[pn]={}
 for cost,stress in [('fees1c',.01),('fees2c',.02),('cap1c',.01)]:
  rows=[x for x in pools[cost] if start<=x['close']<end and (not noncrypto or x['asset'] not in b.ASSETS[:4])]
  results[pn][cost]={}
  for name in names:
   fills,blocks=b.simulate([x for x in rows if allowed(x,name)],stress=stress,**params)
   results[pn][cost][name]=dict(**b.summary(fills),blocked=blocks)
(P/'continuation_results.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results['matched10d']))
