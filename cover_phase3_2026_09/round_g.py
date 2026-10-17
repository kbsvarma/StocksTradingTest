from pathlib import Path
import numpy as np,pandas as pd
from numba import njit
from round_c import inputs,replay
from round_a import stats,sim
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09';prices=pd.read_parquet(V/'long_eligible_prices.parquet');pairs=pd.read_csv(V/'VALIDATED_LOWER_ALL.csv')[['a','b']];rows=[];paths={}
@njit(cache=True)
def expert_weights(core,stock,friction):
 ew=np.linspace(0.,1.,11);wealth=np.ones(11);out=np.full(len(core),.5)
 for i in range(1,len(core)):
  a=core[i]/core[i-1]-1;b=stock[i]/stock[i-1]-1
  for j in range(11):
   ret=ew[j]*a+(1-ew[j])*b;drift=ew[j]*(1+a)/(1+ret);wealth[j]*=(1+ret)*(1-abs(drift-ew[j])*friction)
  out[i]=np.sum(wealth*ew)/wealth.sum()
 return out
if __name__=='__main__':
 for parent in ['baseline','ridge10','width20']:
  for case,fee,slip,sfee in [('stress',7.,1,.001),('severe',14.,2,.0025)]:
   for period,start,end in [('full','2018-05-01','2026-07-02'),('early','2018-05-01','2021-12-31'),('discovery','2022','2025-12-31'),('recent','2026','2026-07-02')]:
    ix,ev,cl,ref,di,ex=inputs(parent,case,prices,start,end);flag=np.r_[True,ix.to_period('W')[1:]!=ix.to_period('W')[:-1]];loc=np.flatnonzero(di>=0)
    for a,b in pairs.itertuples(index=False,name=None):
     sp=prices.loc[ix,[a,b]].to_numpy();sn,_,se=sim(sp,np.ones(sp.shape,dtype=bool),np.ones(len(ix),dtype=bool),0,sfee);sn[-1]/=(1-sfee*se[-1]);weights=expert_weights(ref[loc],sn,2*sfee)
     for capital in [100000.,250000.]:
      for name,mode,targets in [('static_blend',0,None),('weekly_entry',2,None),('universal_entry',2,weights)]:
       p,fees,resizes,peak,deficit=replay(ev,cl,ref,di,ex,sp,flag,capital,mode,True,fee,slip,sfee,.5,False,None,.3,targets)
       rows.append(dict(parent=parent,case=case,period=period,a=a,b=b,capital=capital,design=name,fees=fees,min_margin20=deficit,min_close_margin30=p[:,8].min(),mean_target=weights.mean() if targets is not None else .5,final_target=weights[-1] if targets is not None else .5,**stats(p[:,0]/capital,ix)))
       if capital==100000 and period=='full':paths[f'{parent}_{case}_{a}_{b}__{name}']=pd.Series(p[:,0]/capital,index=ix)
    print(parent,case,period,flush=True)
 d=pd.DataFrame(rows);d.to_csv(P/'round_g_scores.csv',index=False);pd.DataFrame(paths).to_parquet(P/'round_g_paths.parquet');keys=['parent','case','period','a','b','capital'];base=d[d.design=='static_blend'][keys+['cagr','maxdd','sharpe']].rename(columns={k:'static_'+k for k in ['cagr','maxdd','sharpe']});d=d.merge(base,on=keys);d['cagr_change']=d.cagr-d.static_cagr;d['dd_change']=d.maxdd-d.static_maxdd;d['sharpe_change']=d.sharpe-d.static_sharpe;d.to_csv(P/'round_g_comparisons.csv',index=False)
 print(d[(d.capital==100000)&d.period.isin(['full','early','discovery'])&d.design.ne('static_blend')].groupby(['a','b','design']).agg(min_edge=('cagr_change','min'),avg_edge=('cagr_change','mean'),min_dd=('dd_change','min')).sort_values('min_edge',ascending=False).head(15).round(5).to_string())
