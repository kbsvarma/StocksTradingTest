"""Incremental opportunity test with current rules, offline pooled $100 only."""
from common import *
import engine as e
import importlib.util,sys,pickle
from collections import Counter
old=P.parent/'kalshi_loss_review_2026_10_14'
spec=importlib.util.spec_from_file_location('incumbent_research',old/'research.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
sys.path.insert(0,str(P/'fx'))
import analyze as fx
lo=ts('2026-10-08T04:00:00Z');hi=ts('2026-10-14T15:30:00Z')
ms=pickle.loads((old/'recent_features.pickle').read_bytes())+pickle.loads((old/'noncrypto_features.pickle').read_bytes())
ms=[m for m in ms if lo<=m['close']<hi and m['asset']!='COPPER']
results={};ledgers={};controls={};pools={}
for label,mode,stress in [('quote1c','base',.01),('quote2c','base',.02),('cap1c','cap',.01),('delay60_1c','delay60',.01)]:
 pp=[x for x in r.pool_for(ms,mode) if r.keep(x,'btc_z300')]
 # Check the earlier model before changing its fill convention to match this study.
 aa,_=r.simulate(pp,stress);controls[label]=r.metrics(aa)
 for x in pp:
  x['entry']=max(x['entry'],x['ask']);x['fee']=e.fee(x['entry']);x['priority']=0 if x['lane']==r.Z2 else 1;x['reserve']=4.75 if x['priority']==0 else 5
 pp=[x for x in pp if x['entry']<=x['cap']+1e-8]
 fp,_=fx.opportunities(fx.CONFIG[1],{},'cap' if mode=='cap' else mode)
 fp=[x for x in fp if lo<=x['close']<hi and x['asset'] in ('AUDUSD','USDCAD')]
 for x in fp:x['priority']=0 if x['lane']=='original' else 1
 ip=read(P/'indices_pools.json.gz')['strict_z2_z3'][label]
 ip=[x for x in ip if lo<=x['close']<hi and x['asset']=='DJI']
 ic=[x for x in read(P/'indices_pools.json.gz')['eur_curve'][label] if lo<=x['close']<hi and x['asset']=='DJI']
 pools[label]=dict(incumbent=pp,new_fx=fp,dow=ip,dow_curve=ic)
 results[label]={};ledgers[label]={}
 for name,extra in [('incumbent',[]),('plus_AUD',[x for x in fp if x['asset']=='AUDUSD']),('plus_CAD',[x for x in fp if x['asset']=='USDCAD']),('plus_AUD_CAD',fp),('plus_Dow',ip),('plus_AUD_CAD_Dow',fp+ip),('plus_Dow_curve',ic),('plus_CAD_Dow_curve',[x for x in fp if x['asset']=='USDCAD']+ic)]:
  ll,diag=e.portfolio(pp+extra,stress);ledgers[label][name]=ll;z=dict(**e.metrics(ll),portfolio=diag,by_asset={a:e.metrics([x for x in ll if x['asset']==a]) for a in sorted({x['asset'] for x in ll})})
  if name!='incumbent':
   baseline=ledgers[label]['incumbent'];oldkeys={x['ticker'] for x in baseline};newkeys={x['ticker'] for x in ll}
   z.update(added=e.metrics([x for x in ll if x['ticker'] not in oldkeys]),removed=e.metrics([x for x in baseline if x['ticker'] not in newkeys]),net_trade_change=len(ll)-len(baseline),net_pnl_change=sum(x['net'] for x in ll)-sum(x['net'] for x in baseline))
  results[label][name]=z
 print(label,{k:{f:v[f] for f in ('trades','wins','losses','net','drawdown')} for k,v in results[label].items()},flush=True)
save(P/'combined_results.json',results);save(P/'combined_ledgers.json.gz',ledgers);save(P/'combined_pools.json.gz',pools);save(P/'combined_controls.json',dict(period=[lo,hi],old_execution_controls=controls,primary_convention='Pooled $100, full fresh opportunity replays, one asset/expiry, worse signal/next ask, original BTC z3, shared25/$25/$20. No current-balance or routing claim.'))
