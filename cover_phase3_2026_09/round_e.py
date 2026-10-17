from pathlib import Path
import numpy as np,pandas as pd
from round_c import inputs,replay
from round_a import stats
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09';prices=pd.read_parquet(V/'long_eligible_prices.parquet');pairs=pd.read_csv(V/'VALIDATED_LOWER_ALL.csv')[['a','b']];rows=[];paths={}
for parent in ['baseline','ridge10','width20']:
 for case,fee,slip,sfee in [('stress',7.,1,.001),('severe',14.,2,.0025)]:
  for period,start,end in [('full','2018-05-01','2026-07-02'),('early','2018-05-01','2021-12-31'),('discovery','2022','2025-12-31'),('recent','2026','2026-07-02')]:
   ix,ev,cl,ref,di,ex=inputs(parent,case,prices,start,end);flags={'daily':np.ones(len(ix),dtype=bool),'weekly':np.r_[True,ix.to_period('W')[1:]!=ix.to_period('W')[:-1]],'monthly':np.r_[True,ix.to_period('M')[1:]!=ix.to_period('M')[:-1]]}
   for a,b in pairs.itertuples(index=False,name=None):
    for capital in [50000.,100000.,250000.]:
     for inner in ['daily','weekly']:
      for name,mode in [('static_blend',0),('weekly_entry',2),('monthly_entry',3)]:
       p,fees,resizes,peak,margin=replay(ev,cl,ref,di,ex,prices.loc[ix,[a,b]].to_numpy(),flags.get(name.split('_')[0],flags['daily']),capital,mode,True,fee,slip,sfee,.5,False,flags[inner])
       rows.append(dict(parent=parent,case=case,period=period,a=a,b=b,capital=capital,inner=inner,design=name,fees=fees,resizes=resizes,peak_notional=peak,min_margin20=margin,**stats(p[:,0]/capital,ix)))
       if capital==100000 and period=='full':paths[f'{parent}_{case}_{a}_{b}_{inner}__{name}']=pd.Series(p[:,0]/capital,index=ix)
   print(parent,case,period,'done',flush=True)
d=pd.DataFrame(rows);d.to_csv(P/'round_e_scores.csv',index=False);pd.DataFrame(paths).to_parquet(P/'round_e_paths.parquet');keys=['parent','case','period','a','b','capital','inner'];base=d[d.design=='static_blend'][keys+['cagr','maxdd','sharpe']].rename(columns={k:'static_'+k for k in ['cagr','maxdd','sharpe']});d=d.merge(base,on=keys);d['cagr_change']=d.cagr-d.static_cagr;d['dd_change']=d.maxdd-d.static_maxdd;d['sharpe_change']=d.sharpe-d.static_sharpe;d.to_csv(P/'round_e_comparisons.csv',index=False)
x=d[(d.capital==100000)&d.period.isin(['full','early','discovery'])&d.design.ne('static_blend')];r=x.groupby(['parent','a','b','inner','design']).agg(min_edge=('cagr_change','min'),avg_edge=('cagr_change','mean'),min_dd=('dd_change','min'));r.to_csv(P/'round_e_robustness.csv');print(r.sort_values('min_edge',ascending=False).head(20).round(5).to_string())
