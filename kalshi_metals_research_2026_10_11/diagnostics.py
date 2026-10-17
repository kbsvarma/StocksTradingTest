"""Independent arithmetic/chronology and live-loss/source diagnosis."""
import sys,pickle,json,gzip,math,statistics,importlib.util,hashlib
from pathlib import Path
from collections import Counter,defaultdict
from decimal import Decimal as D,ROUND_CEILING
import numpy as np
import study as s
P=s.P;markets=pickle.loads((P/'features.pickle').read_bytes());bars=pickle.loads((P/'bars.pickle').read_bytes());lookup={m['ticker']:m for m in markets}
def save(n,x):s.save(n,x)

old=P.parent/'kalshi_noncrypto15m_2026_10_07'
oldfeatures=pickle.loads((old/'features.pickle').read_bytes());matched=0;maxdiff=0
for m in oldfeatures:
 if m['asset'] not in s.ASSETS:continue
 current={f['t']:f for f in lookup[m['ticker']]['features']}
 for f in m['features']:
  assert f['t'] in current
  diff=max(abs(current[f['t']][k]-f[k]) for k in ('z','sigma','ref','spot','ask','bid','rem'))
  assert all(math.isclose(current[f['t']][k],f[k],abs_tol=1e-8,rel_tol=1e-7) for k in ('z','sigma','ref','spot','ask','bid','rem')),(m['ticker'],diff);maxdiff=max(maxdiff,diff);matched+=1
# Reproduce saved old simulation on its own unchanged universe/settings.
sys.path.insert(0,str(old/'source'));spec=importlib.util.spec_from_file_location('legacy_replay',old/'replay.py');legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
expected=json.loads((old/'results.json').read_text())['opening_plus1c']['periods']['pooled'];control={}
for a in s.ASSETS:
 pool,_=legacy.opportunities([m for m in oldfeatures if m['asset']==a]);trades,_=legacy.portfolio(pool,.01);metrics=legacy.metrics(trades)
 for k in ('trades','wins','losses','net','settled_drawdown'):assert abs(metrics[k]-expected[a][k])<1e-7,(a,k,metrics[k],expected[a][k])
 control[a]={k:metrics[k] for k in ('trades','wins','losses','net','settled_drawdown')}

actual=json.loads((P/'actual_metal_orders.json').read_text());lossaudit=[];actualparity=[]
for r in actual:
 signal=r['signal'];m=lookup[r['ticker']];f=next(x for x in m['features'] if x['t']==signal['decision_ts'])
 points=[bars['COPPER'][t] for t in range(int(f['t'])-3660,int(f['t']),60)]
 # Production evidence hash uses the ordered price array encoded as JSON; scalar parity independent below.
 diff=max(abs(f[k]-float(signal[j])) for k,j in (('z','z'),('spot','spot'),('ref','strike_proxy'),('sigma','sigma')))
 assert diff<1e-8,(r['ticker'],diff)
 actualparity.append(dict(ticker=r['ticker'],difference=diff))
 if D(r['pnl'])<0:
  common={k:f[k] for k in ('rem','z','zrob','zh','zprev','volratio','jump','zero_fraction','flat_minutes','us','hour','ref','spot','ask')}
  common.update(ticker=r['ticker'],net=r['pnl'],won=False,source_issues=m['issues'],modeled_gaussian_win=.5*(1+math.erf(f['z']/math.sqrt(2))),actual_paid=float(r['cost'])/float(r['filled']),actual_breakeven=(float(r['cost'])+float(r['fees']))/float(r['filled']))
  common['path']=[{k:x[k] for k in ('t','rem','z','ask','spot')} for x in m['features'] if x['t']>=f['t']]
  lossaudit.append(common)
save('loss_mechanism_audit.json',lossaudit)

# Calibration of FIRST qualifying price/cushion signal per market, without using fill outcomes for selection.
cal={}
for a in s.ASSETS:
 allrows=[]
 for m in markets:
  if m['asset']!=a:continue
  for f in m['features']:
   if 1<=f['rem']<=10 and .8<=f['ask']<.95 and f['ask']-f['bid']<=.02000001 and f['z']>=1.5:
    allrows.append(dict(f,won=m['result']==int(f['side']==1),date=s.day(m['close'])));break
 tables={}
 for field,bins in (('z',[(1.5,2),(2,2.5),(2.5,3),(3,99)]),('rem',[(1,3),(3,6),(6,11)]),('volratio',[(0,1),(1,1.5),(1.5,99)]),('hour',[(0,8),(8,16),(16,24)])):
  table=[]
  for lo,hi in bins:
   rr=[x for x in allrows if lo<=x[field]<hi]
   table.append(dict(lo=lo,hi=hi,n=len(rr),wins=sum(x['won'] for x in rr),losses=sum(not x['won'] for x in rr),win_rate=np.mean([x['won'] for x in rr]).item() if rr else None,mean_quote=np.mean([x['ask'] for x in rr]).item() if rr else None,normal_prediction=np.mean([.5*(1+math.erf(x['z']/math.sqrt(2))) for x in rr]).item() if rr else None))
  tables[field]=table
 cal[a]=tables
save('signal_calibration.json',cal)
# independent sample source math, not labels used as predictors
rng=np.random.default_rng(711);sample=[(m,f) for m in markets for f in m['features']];indices=rng.choice(len(sample),min(2000,len(sample)),replace=False)
for i in indices:
 m,f=sample[i];vv=[bars[m['asset']][t] for t in range(f['t']-3660,f['t'],60)];sigma=statistics.pstdev(math.log(y/x) for x,y in zip(vv,vv[1:]));z=f['side']*(vv[-1]/bars[m['asset']][m['start']-60]-1)/(sigma*math.sqrt(f['rem']))
 assert math.isclose(z,f['z'],abs_tol=1e-7,rel_tol=1e-7)
 assert m['start']<f['t']<m['close']<=m['settled']
modelaudit=json.loads((P/'model_audit.json').read_text());assert all(r['last_settled']<r['week'] for r in modelaudit)
save('input_validation.json',dict(passed=True,old_features_matched=matched,max_feature_difference=maxdiff,old_controls=control,actual_live_features_matched=len(actualparity),max_actual_difference=max(x['difference'] for x in actualparity),independent_source_checks=len(indices),causal_models=len(modelaudit),price_source_revision_note='Historical first-arrival latency not inferred from chart timestamps'))
print('INPUT VALIDATION PASS',matched,control,flush=True)

if (P/'final_ledgers.json.gz').exists():
 with gzip.open(P/'final_ledgers.json.gz','rt') as f:ledgers=json.load(f)
 checked=0;stats={};frozen=json.loads((P/'choices_frozen.json').read_text())['choices']
 for cid,scenarios in ledgers.items():
  for mode,assets in scenarios.items():
   for asset,periods in assets.items():
    for period,rows in periods.items():
     assert len({r['ticker'] for r in rows})==len(rows)
     for r in rows:
      assert r['t']<r['entered']<r['close']<=r['settled']
      assert r['floor']-1e-8<=r['entry']<=r['cap']+1e-8
      exact=5*(r['won']-D(str(r['entry'])))-D(str(r['fee']))-D(str(r['friction']))
      assert abs(float(exact)-r['net'])<1e-8
      checked+=1
 for a,c in frozen.items():
  if c['selected'] is None:continue
  cid=str(c['selected']);stats[a]={}
  for p in ('pooled','confirmation','recent'):
   rows=ledgers[cid]['base'][a][p];lo,hi=s.PERIODS[p];days=sorted({s.day(t) for t in range(lo,hi,86400)}|{s.day(r['settled']) for r in rows});daily=defaultdict(float)
   for r in rows:daily[s.day(r['settled'])]+=r['net']
   d=np.array([daily[x] for x in days]);boots=np.sum(rng.choice(d,(5000,len(d)),replace=True),axis=1)
   stats[a][p]=dict(day_bootstrap_95=np.quantile(boots,[.025,.975]).tolist(),extra_loss_impact='Replacing one 5-contract winner by a loser reduces net $5; not a new trade fee',net_with_one_extra_loss=sum(r['net'] for r in rows)-5,trade_count=len(rows),calendar_blocks=len(d))
 save('robustness.json',stats);save('accounting_validation.json',dict(passed=True,trade_scenario_rows=checked,unique_ownership=True,chronology=True,price_caps=True,decimal_pnl=True))
 print('ACCOUNTING PASS',checked,flush=True)
