from pathlib import Path
import pandas as pd,numpy as np,pandas_market_calendars as mcal,json
P=Path(__file__).resolve().parent;old=pd.read_parquet(P/'expanded_eligible_prices.parquet');dates=mcal.get_calendar('NYSE').schedule('2000-01-01','2026-09-11').index;cols={};audit=[]
for s in old.columns:
 f=P/'long_daily'/(s+'.parquet')
 if not f.exists():cols[s]=old[s].reindex(dates);audit.append({'symbol':s,'status':'old_history_fallback'});continue
 d=pd.read_parquet(f);d.index=pd.to_datetime(d.index.date);q=d['Adj Close'];q=q[~q.index.duplicated(keep='last')].reindex(dates);cmp=pd.concat([q.pct_change(fill_method=None),old[s].pct_change(fill_method=None)],axis=1).dropna();diff=(cmp.iloc[:,0]-cmp.iloc[:,1]).abs();flags=int((diff>.0001).sum());audit.append({'symbol':s,'status':'ok','return_differences_over_1bp':flags,'max_return_difference':float(diff.max())});cols[s]=q
pd.DataFrame(cols,index=dates).to_parquet(P/'long_eligible_prices.parquet');pd.DataFrame(audit).to_csv(P/'long_refresh_audit.csv',index=False);print('DONE',len(cols),flush=True)
