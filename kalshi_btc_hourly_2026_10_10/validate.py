"""Independent invariants and parity against previously verified portfolio replay."""
import gzip,json,math,importlib.util,sys,subprocess
from pathlib import Path
from decimal import Decimal as D
import study as s
def run():
 report={}
 bars={t:100+math.sin(t/300) for t in range(0,64*60,60)}
 stats=s.stats(bars);assert 3660 in stats
 modified=dict(bars);modified[3660]=1000
 assert s.stats(modified)[3660]==stats[3660],'Future price changed an earlier decision'
 del modified[60];assert 3660 not in s.stats(modified),'Missing complete minute was filled'
 report['causal_inputs_and_gap_rejection']=True
 for side in (-1,1):
  m=dict(strike_type='greater',floor_strike=100,cap_strike=None)
  assert math.isclose(s.distance(m,103,side),side*.03)
 m=dict(strike_type='between',floor_strike=100,cap_strike=110)
 assert s.distance(m,105,1)>0 and s.distance(m,105,-1)<0
 assert s.distance(m,115,-1)>0 and s.distance(m,95,-1)>0
 report['range_boundary_signs']=True
 # Fee computations checked independently using integer centicent debit.
 for p in (.85,.90,.93,.939,.95,.97,.975):
  raw=(D('.07')*5*D(str(p))*(1-D(str(p))))
  fee=raw.quantize(D('.000001'),rounding='ROUND_CEILING')
  paid=((D(str(p))*5+fee)*10000).to_integral_value(rounding='ROUND_CEILING')/10000-D(str(p))*5
  assert abs(s.fee(p)-float(paid))<1e-10
 report['fee_rounding']=True
 with gzip.open(s.P.parent/'kalshi_wti_2026_10_07/budget_opportunities.json.gz','rt') as f:pools=json.load(f)
 # A fresh interpreter prevents this study's module name from shadowing the old study.
 code='''import sys,json,gzip
from pathlib import Path
P=Path.cwd();sys.path.insert(0,str(P));import budget_replay as old
with gzip.open(P.parent/'kalshi_wti_2026_10_07/budget_opportunities.json.gz','rt') as f:pools=json.load(f)
lo,hi=old.s.r.ts('2026-09-23T00:00:00Z'),old.s.r.ts('2026-10-03T00:00:00Z');out={}
for cost,stress in [('fees1c',.01),('fees2c',.02),('cap1c',.01)]:
 for copper in (True,False):
  rows=[x for x in pools[cost] if lo<=x['close']<hi and (copper or x['asset']!='COPPER')]
  b,_=old.simulate(rows,budget=20,exposure=25,stress=stress,max_contracts=25,z3_contracts=25,z3_exposure=25,z3_budget=25)
  out[cost+'/'+str(copper)]={x['ticker']:[x['entry'],x['net'],x['entered']] for x in b}
print(json.dumps(out))
'''
 expected=json.loads(subprocess.check_output([sys.executable,'-c',code],cwd=s.P.parent/'kalshi_capacity25_2026_10_07',text=True))
 lo,hi=s.ts('2026-09-23T00:00:00Z'),s.ts('2026-10-03T00:00:00Z')
 for cost,stress in [('fees1c',.01),('fees2c',.02),('cap1c',.01)]:
  for copper in (True,False):
   rows=[x for x in pools[cost] if lo<=x['close']<hi and (copper or x['asset']!='COPPER')]
   a,_=s.simulate(rows,stress)
   aa={x['ticker']:[x['entry'],x['net'],x['entered']] for x in a};bb=expected[cost+'/'+str(copper)]
   assert aa==bb,(cost,copper,'portfolio mismatch')
   report[f'old_portfolio/{cost}/copper={copper}']=s.metrics(a)
 s.save(s.P/'validation.json',report);print(json.dumps({k:(v if isinstance(v,bool) else {x:v[x] for x in ('trades','net','drawdown')}) for k,v in report.items()},indent=2))
if __name__=='__main__':run()
