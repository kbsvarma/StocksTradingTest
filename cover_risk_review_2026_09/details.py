from pathlib import Path
import pandas as pd,numpy as np,json
from scipy.optimize import brentq
P=Path(__file__).resolve().parent;O=P.parent/'cover_exhaustive_2026_09'
T=pd.concat([pd.read_csv(P/'LOWER_RISK_TOP10.csv'),pd.read_csv(P/'HIGHER_RISK_TOP10.csv')],ignore_index=True);Q=pd.read_parquet(O/'price_matrix.parquet');rows=[];curves=[];checks=[];features=[]
def ledger(px,c,rebal):
 shares=1000/(1+c)*np.array([.5,.5])/px.iloc[0].to_numpy();w=[1000.];turn=[]
 for k,v in enumerate(px.iloc[1:].to_numpy()):
  val=shares*v;before=val.sum()
  if k==len(px)-2:after=before*(1-c);tr=before;shares*=1-c
  elif rebal:
   after=brentq(lambda x:x+c*np.abs(.5*x-val).sum()-before,0,before,xtol=1e-10);tr=np.abs(.5*after-val).sum();shares=.5*after/v
  else:after=before;tr=0.
  w.append(after);turn.append(tr/before)
 return np.array(w),np.array(turn)
for _,r in T.iterrows():
 pair=r.a+' / '+r.b;p=Q[[r.a,r.b]];pre=p.loc[:'2025-12-31'].iloc[int(r.pre_start_index):];end=p.index[-1];segments={'pre2026':pre,'discovery':p.loc['2021-12-31':'2025-12-31'],'2020':p.loc['2019-12-31':'2020-12-31'],'confirmation':p.loc['2025-12-31':end],'continuous_2022_2026':p.loc['2021-12-31':end]}
 for period,px in segments.items():
  if len(px)<2 or px.isna().any().any():continue
  years=(px.index[-1]-px.index[0]).days/365.25
  for bps in [5,10,25,50]:
   for rebal in [True,False]:
    w,tr=ledger(px,bps/10000,rebal);peak=np.maximum.accumulate(w);dd=w/peak-1;j=dd.argmin();i=w[:j+1].argmax();rec=np.flatnonzero(w[j:]>=w[i]);recovery=px.index[j+rec[0]] if len(rec) else pd.NaT
    below=w<peak*(1-1e-10);run=longest=0
    for v in below:run=run+1 if v else 0;longest=max(longest,run)
    cagr=(w[-1]/1000)**(1/years)-1
    row=dict(pair=pair,list=r['list'],rank=r['rank'],period=period,strategy='daily_50_50' if rebal else 'hold_50_50',cost_bps=bps,start=str(px.index[0].date()),end=str(px.index[-1].date()),terminal_1000=w[-1],total_return=w[-1]/1000-1,cagr=cagr,maxdd=dd.min(),dd_peak=str(px.index[i].date()),dd_trough=str(px.index[j].date()),dd_recovery=str(recovery.date()) if pd.notna(recovery) else 'not recovered by period end',longest_underwater_sessions=longest,median_turnover=np.median(tr[:-1]),p90_turnover=np.quantile(tr[:-1],.9))
    rows.append(row)
    if bps==5:
     for dt,v in zip(px.index,w):curves.append(dict(date=dt,wealth=v,pair=pair,period=period,strategy=row['strategy']))
    if rebal and period in ['discovery','confirmation','pre2026'] and bps in [5,10]:
     prefix='pre' if period=='pre2026' else period;expected=r[f'{prefix}_cagr_{bps}bps'];error=abs(expected-cagr);assert error<1e-8,(pair,period,error);checks.append(dict(pair=pair,period=period,bps=bps,error=error))
  ret=px.pct_change().iloc[1:];lg=np.log1p(ret);both=(ret<0).all(axis=1);roll=lg[r.a].rolling(63).corr(lg[r.b]);marketdown=ret.mean(axis=1)<0
  features.append(dict(pair=pair,period=period,correlation=lg.corr().iloc[0,1],both_down_fraction=both.mean(),rolling63_corr_max=roll.max(),rolling63_corr_min=roll.min(),a_total_return=px.iloc[-1,0]/px.iloc[0,0]-1,b_total_return=px.iloc[-1,1]/px.iloc[0,1]-1))
 print(pair,'checked',flush=True)
pd.DataFrame(rows).to_csv(P/'pair_details.csv',index=False);pd.DataFrame(curves).to_parquet(P/'pair_curves.parquet',index=False);pd.DataFrame(checks).to_csv(P/'ledger_checks.csv',index=False);pd.DataFrame(features).to_csv(P/'pair_behavior.csv',index=False)
