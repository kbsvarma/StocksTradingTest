import json,pickle,gzip,hashlib,subprocess,datetime as dt,math
from pathlib import Path
from collections import defaultdict,Counter
import numpy as np
import study as s
P=s.P;markets=pickle.loads((P/'features.pickle').read_bytes());pred=pickle.loads((P/'predictions.pickle').read_bytes());lookup={m['ticker']:m for m in markets};actual=json.loads((P/'actual_metal_orders.json').read_text());actual_comparison=[]
for r in actual:
 m=lookup[r['ticker']];signal=r['signal'];T=int(signal['decision_ts']);side=1 if r['side']=='yes' else -1;f=next(f for f in m['features'] if f['t']==T);pair=s.quote(m['candles'].get(T+60));px=(pair[1] if side==1 else 1-pair[0]) if pair else None;cap=float(signal['execution_cap']);candle_fits=px is not None and .8-1e-8<=px<=cap+1e-8
 passes=[]
 for ff in m['features']:
  if not 3<=ff['rem']<=10 or ff['ask']-ff['bid']>.02000001:continue
  for ln,z,floor,ceil in (('original',2,.85,.95),('premium',3,.93,.98)):
   if floor-1e-8<=ff['ask']<ceil-1e-8 and ff['z']>=z:passes.append(dict(t=ff['t'],z=ff['z'],side=ff['side'],lane=ln))
 actual_comparison.append(dict(ticker=r['ticker'],won=r['side']==r['result'],pnl=r['pnl'],signal_ask=signal['signal_ask'],next_open=px,actual_average=float(r['cost'])/float(r['filled']),cap=cap,candle_would_fill=candle_fits,strict_z2_qualifiers=passes))
s.save('actual_vs_quote_proxy.json',actual_comparison)

# Walk-forward calibration quality on each fixed horizon; each market counted once per horizon.
cal=[]
for a in s.ASSETS:
 for rem in (3,5,10):
  for period,(lo,hi) in s.PERIODS.items():
   if period not in ('selection','confirmation','recent'):continue
   for kind in ('quote','z','rich','empirical'):
    rr=[(m,f,pred.get((m['ticker'],f['t'],kind))) for m in markets if m['asset']==a and lo<=m['close']<hi for f in m['features'] if f['rem']==rem];rr=[x for x in rr if x[2] is not None]
    if not rr:continue
    brier=np.mean([(p-m['result'])**2 for m,f,p in rr]);market=np.mean([((f['ybid']+f['yask'])/2-m['result'])**2 for m,f,p in rr]);cal.append(dict(asset=a,rem=rem,period=period,model=kind,n=len(rr),brier=float(brier),market_brier=float(market)))
s.save('probability_validation.json',cal)

# Fixed price/quote-neighbor robustness for the original frozen three-minute-to-five-minute choice and Silver filter.
configs=[]
for a,c in (('COPPER',s.CONFIG[3]),('SILVER',s.CONFIG[20])):
 variants=[dict(c)]
 if a=='COPPER':
  for z in (1.25,1.75,2):variants.append(dict(c,z=z,name=f'neighbor_z{z}'))
  for lo,hi in ((2,5),(4,5),(3,6)):variants.append(dict(c,lo=lo,hi=hi,name=f'neighbor_time{lo}_{hi}'))
 else:
  # Change z while retaining the independently defined four-sigma jump screen.
  for z in (1.75,2.25,2.5):variants.append(dict(c,z=z,name=f'neighbor_z{z}'))
  for lo,hi in ((3,7),(5,10),(2,10)):variants.append(dict(c,lo=lo,hi=hi,name=f'neighbor_time{lo}_{hi}'))
 for v in variants:
  pool,_=s.opportunities(markets,v,pred);data={per:s.evaluate(pool,a,per,.02)[0] for per in s.PERIODS};configs.append(dict(asset=a,rule=v,scenario='fees_plus2c',results=data))
s.save('neighbor_diagnostics.json',dict(note='Post-selection diagnostics only; frozen choices unchanged. New neighbors are not promoted from later results.',variants=configs))

# Confidence for original Copper control and selected Silver; not adjusted for rule selection.
with gzip.open(P/'final_ledgers.json.gz','rt') as f:ledgers=json.load(f)
rng=np.random.default_rng(917);uncertainty={}
for a,cid in (('COPPER','0'),('SILVER','20'),('GOLD','3')):
 uncertainty[a]={}
 for per in ('pooled','confirmation','recent'):
  rr=ledgers[cid]['base'][a][per];lo,hi=s.PERIODS[per];dd=defaultdict(float)
  for r in rr:dd[s.day(r['settled'])]+=r['net']
  dates=sorted({s.day(t) for t in range(lo,hi,3600)}|set(dd));values=np.array([dd[d] for d in dates]);n=len(rr);w=sum(r['won'] for r in rr);p=w/n if n else 0;z=1.95996398454;center=(p+z*z/(2*n))/(1+z*z/n) if n else 0;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n) if n else 0
  ci=np.quantile(rng.choice(values,(10000,len(values)),replace=True).sum(1),[.025,.975]).tolist()
  uncertainty[a][per]=dict(day_bootstrap95=ci,wilson_win95=[center-half,center+half],net_one_extra_loss=sum(r['net'] for r in rr)-5,calendar_day_blocks=len(dates))
s.save('recommendation_uncertainty.json',uncertainty)

# Preserve live bot and source checkpoint; read state only.
L=P.parent/'kalshi_revalidation_2026_09_12/live_v1';before=json.loads((P/'preservation_before.json').read_text());hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in L.glob('*.py')};assert hashes==before['sources']
flags={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (L/'state').iterdir() if p.name.startswith(('ARM','HALT'))};assert flags==before['flags']
services={n:dict(x.split('=',1) for x in subprocess.check_output(['systemctl','--user','show',n,'-p','MainPID','-p','ActiveState','-p','NRestarts'],text=True).splitlines()) for n in before['services']};assert services==before['services']
b=P.parent/'research_checkpoints/kalshi_stable_baseline_2026_09_28';base=json.loads((b/'manifest.json').read_text())['sha256'];assert all(hashlib.sha256((b/'source'/n).read_bytes()).hexdigest()==v for n,v in base.items())
s.save('preservation_after.json',dict(passed=True,sources=len(hashes),checkpoint_files=len(base),services=services,flags_unchanged=True))
print('Actual-vs-candle',Counter((r['won'],r['candle_would_fill']) for r in actual_comparison));print('PRODUCTION UNCHANGED')
