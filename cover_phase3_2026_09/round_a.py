from pathlib import Path
import numpy as np,pandas as pd
from numba import njit
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09'
@njit(cache=True)
def sim(pr,eligible,flags,mode,fee,band=0.):
 n=len(pr);q=np.zeros(2);cash=1.;out=np.zeros(n);turn=np.zeros(n);exp=np.zeros(n);prev=np.zeros(2,dtype=np.bool_)
 for i in range(n):
  val=q*pr[i];nav=cash+val.sum();target=np.zeros(2)
  for j in range(2):target[j]=.5 if eligible[i,j] else 0.
  act=i==0 or flags[i]
  if mode==1:act=i==0
  if mode==2:act=i==0 or np.max(np.abs(val/nav-target))>band
  # Trend entry-only control retains each position until its gate changes.
  changed=np.any(eligible[i]!=prev)
  if mode==4:act=i==0 or changed
  if act or changed:
   if mode==4:
    want=val.copy()
    for j in range(2):
     if eligible[i,j] and not prev[j]:want[j]=nav*.5
     if not eligible[i,j]:want[j]=0.
    # No borrowing if another sleeve has grown; scale new requests to cash after sells.
   else:
    post=nav
    for _ in range(20):post=nav-fee*np.abs(target*post-val).sum()
    want=target*post
    if mode==3 and i>0 and not changed:want=(want+val)*.5
   diff=want-val
   for j in range(2):
    if diff[j]<0:cash+=(-diff[j])*(1-fee);q[j]+=diff[j]/pr[i,j];turn[i]+=-diff[j]
   required=np.maximum(diff,0).sum()*(1+fee);scale=min(1.,max(0.,cash)/required) if required>0 else 0.
   for j in range(2):
    if diff[j]>0:amt=diff[j]*scale;cash-=amt*(1+fee);q[j]+=amt/pr[i,j];turn[i]+=amt
  prev=eligible[i].copy();out[i]=cash+(q*pr[i]).sum();exp[i]=(q*pr[i]).sum()/out[i]
  assert cash> -1e-8
 # terminal liquidation common across variants
 out[-1]-=fee*(q*pr[-1]).sum();turn[-1]+=(q*pr[-1]).sum()
 return out,turn,exp

def stats(nav,dates):
 r=pd.Series(nav,index=dates).pct_change().fillna(nav[0]-1);yrs=(dates[-1]-dates[0]).days/365.25
 return dict(cagr=nav[-1]**(1/yrs)-1,terminal=nav[-1],maxdd=float(np.min(nav/np.maximum.accumulate(np.r_[1.,nav])[1:]-1)),sharpe=float(r.mean()/r.std()*np.sqrt(252)),years=yrs)
def main():
 prices=pd.read_parquet(V/'long_eligible_prices.parquet');low=pd.read_csv(V/'VALIDATED_LOWER_ALL.csv');hi=pd.read_csv(V/'AUDITED_HIGHER_TOP10_ALL.csv');pairs=pd.concat([low[['a','b']],hi[['a','b']]]).drop_duplicates();rows=[];paths={}
 for a,b in pairs.itertuples(index=False,name=None):
  p=prices[[a,b]].dropna();eligs={n:(p>p.rolling(n,min_periods=n).mean()).shift(1).fillna(False) for n in [100,200]}
  for period,start,end in [('full','2000','2026-09-11'),('early','2018','2021-12-31'),('discovery','2022','2025-12-31'),('recent','2026','2026-09-11')]:
   d=p.loc[start:end]
   if len(d)<100:continue
   ix=d.index;flags={'daily':np.ones(len(d),dtype=bool),'weekly':np.r_[True,ix.to_period('W')[1:]!=ix.to_period('W')[:-1]],'monthly':np.r_[True,ix.to_period('M')[1:]!=ix.to_period('M')[:-1]]};allgood=np.ones(d.shape,dtype=bool)
   specs=[('hold',allgood,flags['daily'],1,0),('daily',allgood,flags['daily'],0,0),('weekly',allgood,flags['weekly'],0,0),('monthly',allgood,flags['monthly'],0,0),('band5',allgood,flags['daily'],2,.05),('band10',allgood,flags['daily'],2,.1),('half_daily',allgood,flags['daily'],3,0)]
   for n in [100,200]:
    eligible=eligs[n].reindex(ix).to_numpy()
    specs.extend([(f'trend{n}_daily',eligible,flags['daily'],0,0),(f'trend{n}_entry_only',eligible,flags['daily'],4,0)])
   for cost in [5,10,25]:
    for name,el,flag,mode,band in specs:
     nav,t,ex=sim(d.to_numpy(),el,flag,mode,cost/1e4,band);rows.append(dict(a=a,b=b,period=period,cost=cost,design=name,turnover=t.sum(),mean_exposure=ex.mean(),**stats(nav,ix)))
     if period=='full' and cost==10:paths[f'{a}_{b}__{name}']=pd.Series(nav,index=ix)
  print(a,b,'done',flush=True)
 pd.DataFrame(rows).to_csv(P/'round_a_scores.csv',index=False);pd.DataFrame(paths).to_parquet(P/'round_a_paths.parquet')
 d=pd.DataFrame(rows);base=d[d.design=='daily'][['a','b','period','cost','cagr','maxdd']].rename(columns={'cagr':'daily_cagr','maxdd':'daily_dd'});d=d.merge(base,on=['a','b','period','cost']);d['cagr_change']=d.cagr-d.daily_cagr;d['dd_change']=d.maxdd-d.daily_dd;d.to_csv(P/'round_a_comparisons.csv',index=False)
 print(d[(d.cost==10)&(d.period.isin(['early','discovery']))].groupby(['period','design'])[['cagr_change','dd_change']].median().round(4).to_string())
if __name__=='__main__':main()
