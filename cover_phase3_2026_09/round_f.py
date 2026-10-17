from pathlib import Path
import numpy as np,pandas as pd
from round_c import inputs,replay
from round_a import stats
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09';prices=pd.read_parquet(V/'long_eligible_prices.parquet');rows=[];paths={}
for parent in ['baseline','ridge10','width20']:
 for case,fee,slip,sfee in [('stress',7.,1,.001),('severe',14.,2,.0025)]:
  for period,start,end in [('full','2018-05-01','2026-07-02'),('early','2018-05-01','2021-12-31'),('discovery','2022','2025-12-31'),('recent','2026','2026-07-02')]:
   ix,ev,cl,ref,di,ex=inputs(parent,case,prices,start,end);flag=np.r_[True,ix.to_period('W')[1:]!=ix.to_period('W')[:-1]]
   for capital in [100000.,250000.]:
    for margin in [.2,.3]:
     for name,mode,w in [('static_blend',0,.5),('weekly_entry',2,.5),('core',0,1.),('stock_pair',0,0.)]:
      p,fees,resizes,peak,deficit=replay(ev,cl,ref,di,ex,prices.loc[ix,['CI','YUMC']].to_numpy(),flag,capital,mode,True,fee,slip,sfee,w,False,None,margin)
      rows.append(dict(parent=parent,case=case,period=period,capital=capital,margin=margin,design=name,fees=fees,peak_notional=peak,min_margin20=deficit,min_close_margin30=p[:,8].min(),**stats(p[:,0]/capital,ix)))
      if capital==100000 and period=='full':paths[f'{parent}_{case}_{margin}__{name}']=pd.Series(p[:,0]/capital,index=ix)
   print(parent,case,period,flush=True)
d=pd.DataFrame(rows);d.to_csv(P/'round_f_scores.csv',index=False);pd.DataFrame(paths).to_parquet(P/'round_f_paths.parquet');keys=['parent','case','period','capital','margin'];base=d[d.design=='static_blend'][keys+['cagr','maxdd','sharpe']].rename(columns={k:'static_'+k for k in ['cagr','maxdd','sharpe']});d=d.merge(base,on=keys);d['cagr_change']=d.cagr-d.static_cagr;d['dd_change']=d.maxdd-d.static_maxdd;d.to_csv(P/'round_f_comparisons.csv',index=False);print(d[(d.capital==100000)&(d.period=='full')&(d.design=='weekly_entry')].round(5).to_string(index=False))
