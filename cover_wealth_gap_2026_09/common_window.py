from pathlib import Path
import pandas as pd,numpy as np,json
from numba import njit,prange,set_num_threads
set_num_threads(4);P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09'
q=pd.read_parquet(V/'long_eligible_prices.parquet').loc['2004-09-13':'2026-09-11'];q=q.loc[:,q.notna().all()&q.gt(0).all()];prof=pd.read_csv(V/'company_quality_audit.csv').set_index('ticker');q=q[[s for s in q if s in prof.index and bool(prof.loc[s,'company_stock'])]];names=q.columns.to_numpy();px=q.to_numpy().T.copy();aa,bb=np.triu_indices(len(names),1)
@njit(parallel=True,cache=True)
def calculate(px,aa,bb,weight,cost):
 out=np.zeros((len(aa),6))
 for k in prange(len(aa)):
  a,b=aa[k],bb[k];wa=px[a,-1]/px[a,0]*(1-cost)/(1+cost);wb=px[b,-1]/px[b,0]*(1-cost)/(1+cost);w=1/(1+cost);peak=1.;dd=0.
  for t in range(1,px.shape[1]):
   ga=px[a,t]/px[a,t-1];gb=px[b,t]/px[b,t-1];A=weight*ga;B=(1-weight)*gb
   if t==px.shape[1]-1:w*=(A+B)*(1-cost)
   else:
    sign=np.sign(ga-gb);w*=(A+B-cost*sign*(A-B))/(1+cost*sign*(1-2*weight))
   peak=max(peak,w);dd=min(dd,w/peak-1)
  out[k]=np.array([wa,wb,w,w/max(wa,wb),dd,weight*wa+(1-weight)*wb])
 return out
hist=pd.read_csv(P/'historical_quality22.csv').set_index('symbol');ex=set(pd.read_csv(V/'ai_exclusions_final.csv').symbol);rows=[]
for weight in [.5,.55]:
 for cost in [.001,.0025]:
  z=pd.DataFrame(calculate(px,aa,bb,weight,cost),columns=['stock_a_multiple','stock_b_multiple','rebalance_multiple','over_best','maxdd','hold_multiple']);z.insert(0,'b',names[bb]);z.insert(0,'a',names[aa]);z['weight_a']=weight;z['cost_bps']=cost*1e4;z['over_hold']=z.rebalance_multiple/z.hold_multiple;z['no_ai']=~z.a.isin(ex)&~z.b.isin(ex);z['history_proxy']=True
  for side in ['a','b']:z['history_proxy']&=z[side].map(hist.zero_volume_fraction).le(.01)&z[side].map(hist.p10_dollar_volume).ge(1e5)&z[side].map(hist.extreme_4x).eq(0)
  rows.append(z);print(weight,cost,len(z),flush=True)
x=pd.concat(rows,ignore_index=True);x.to_parquet(P/'common22_all_pairs.parquet',index=False);z=x[x.weight_a.eq(.5)&x.cost_bps.eq(10)&x.history_proxy&x.no_ai&x.over_best.ge(4)&x.rebalance_multiple.ge(20)&~x.a.isin(['GME','AMC','MSTR','COIN'])&~x.b.isin(['GME','AMC','MSTR','COIN'])].sort_values('maxdd',ascending=False);z.to_csv(P/'common22_material_candidates.csv',index=False);print(z.head(20).to_string(index=False),flush=True)
(P/'common22_counts.json').write_text(json.dumps(dict(stocks=len(names),pairs=len(aa),start=str(q.index[0].date()),end=str(q.index[-1].date())),indent=2))
