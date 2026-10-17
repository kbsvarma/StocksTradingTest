from pathlib import Path
import numpy as np,pandas as pd
from round_c import inputs,replay
from round_b import PAIRS
from round_a import stats
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09'
prices=pd.read_parquet(V/'long_eligible_prices.parquet');rows=[];paths={}
for parent in ['baseline','ridge10','width20']:
 for case,fee,slip,sfee in [('stress',7.,1,.001),('severe',14.,2,.0025)]:
  for period,start,end in [('full','2018-05-01','2026-07-02'),('early','2018-05-01','2021-12-31'),('discovery','2022','2025-12-31'),('recent','2026','2026-07-02')]:
   ix,ev,cl,ref,di,ex=inputs(parent,case,prices,start,end);flags={'daily':np.ones(len(ix),dtype=bool),'weekly':np.r_[True,ix.to_period('W')[1:]!=ix.to_period('W')[:-1]],'monthly':np.r_[True,ix.to_period('M')[1:]!=ix.to_period('M')[:-1]]}
   for a,b in PAIRS:
    for capital in [50000.,100000.,250000.]:
     for name,mode in [('daily',1),('weekly',2),('monthly',3),('half_daily',4)]:
      p,fees,resizes,peak,margin=replay(ev,cl,ref,di,ex,prices.loc[ix,[a,b]].to_numpy(),flags.get(name,flags['daily']),capital,mode,True,fee,slip,sfee,.5,False)
      rows.append(dict(parent=parent,case=case,period=period,a=a,b=b,capital=capital,integer=True,design=name+'_entry',fees=fees,resizes=resizes,peak_notional=peak,min_margin20=margin,**stats(p[:,0]/capital,ix)))
      if capital==100000 and case=='stress' and period=='full':paths[f'{parent}_{a}_{b}__{name}_entry']=pd.Series(p[:,0]/capital,index=ix)
   print(parent,case,period,'done',flush=True)
d=pd.DataFrame(rows);d.to_csv(P/'round_d_scores.csv',index=False);pd.DataFrame(paths).to_parquet(P/'round_d_paths.parquet');c=pd.read_csv(P/'round_c_scores.csv');keys=['parent','case','period','a','b','capital','integer'];base=c[c.design=='static_blend'][keys+['cagr','maxdd','sharpe']].rename(columns={k:'static_'+k for k in ['cagr','maxdd','sharpe']});d=d.merge(base,on=keys);d['cagr_change']=d.cagr-d.static_cagr;d['dd_change']=d.maxdd-d.static_maxdd;d['sharpe_change']=d.sharpe-d.static_sharpe;d.to_csv(P/'round_d_comparisons.csv',index=False)
x=d[(d.capital==100000)&d.period.isin(['full','early','discovery'])];print(x.groupby(['parent','a','b','design']).agg(min_edge=('cagr_change','min'),avg_edge=('cagr_change','mean'),min_dd=('dd_change','min')).sort_values('min_edge',ascending=False).head(15).round(5).to_string())
