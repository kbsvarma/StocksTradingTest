from pathlib import Path
import numpy as np,pandas as pd,json
from round_c import inputs,replay
from round_a import stats
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09';prices=pd.read_parquet(V/'long_eligible_prices.parquet');rows=[];actualpaths={}
for parent in ['baseline','ridge10','width20']:
 for case,fee,slip,sfee in [('stress',7.,1,.001),('severe',14.,2,.0025)]:
  for period,start,end in [('full','2018-05-01','2026-07-02'),('early','2018-05-01','2021-12-31'),('discovery','2022','2025-12-31'),('recent','2026','2026-07-02')]:
   ix,ev,cl,ref,di,ex=inputs(parent,case,prices,start,end);flag=np.ones(len(ix),dtype=bool)
   p,*_=replay(ev,cl,ref+50000,di,ex,np.ones((len(ix),2)),flag,100000.,0,True,fee,slip,sfee,1.,False,None,.3)
   rows.append(dict(parent=parent,case=case,period=period,**stats(p[:,0]/100000.,ix)))
   if period=='full':actualpaths[parent+'_'+case]=pd.Series(p[:,0]/100000.,index=ix)
pd.DataFrame(rows).to_csv(P/'unchanged_core_100k.csv',index=False);pd.DataFrame(actualpaths).to_parquet(P/'unchanged_core_100k_paths.parquet')
# Conditional moving-month block inference over the entire final G family.
paths=pd.read_parquet(P/'round_g_paths.parquet');increments={}
for name in paths:
 prefix,design=name.split('__')
 if design=='static_blend':continue
 a=np.log(paths[name]).diff();b=np.log(paths[prefix+'__static_blend']).diff();a.iloc[0]=np.log(paths[name].iloc[0]);b.iloc[0]=np.log(paths[prefix+'__static_blend'].iloc[0]);increments[name]=(a-b).resample('ME').sum()
m=pd.DataFrame(increments);arr=m.to_numpy();rng=np.random.default_rng(20260913);n=len(arr);draws=[]
for _ in range(2000):
 starts=rng.integers(0,n,size=(n+2)//3);loc=np.concatenate([(s+np.arange(3))%n for s in starts])[:n];draws.append(arr[loc].mean(axis=0)*12)
draws=np.asarray(draws);point=arr.mean(axis=0)*12;sd=draws.std(axis=0);critical=np.quantile(np.max(np.abs((draws-point)/np.maximum(sd,1e-12)),axis=1),.95);out=pd.DataFrame({'candidate':m.columns,'annual_log_edge':point,'individual_lo':np.quantile(draws,.025,axis=0),'individual_hi':np.quantile(draws,.975,axis=0),'family_lo':point-critical*sd,'family_hi':point+critical*sd});out.to_csv(P/'family_uncertainty.csv',index=False)
# Attribution only: omit each calendar year's log increment, no re-selection.
r=[]
for name,s in increments.items():
 for y in sorted(set(s.index.year)):
  a=s[s.index.year!=y];r.append(dict(candidate=name,excluded_year=y,annual_log_edge=a.mean()*12))
pd.DataFrame(r).to_csv(P/'leave_year_out_attribution.csv',index=False)
print('family',len(out),'simultaneous positive',int((out.family_lo>0).sum()),'critical',critical)
print(out[out.candidate.str.contains('baseline_stress_CI_YUMC')].to_string(index=False))
