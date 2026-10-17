from build_futures import *
rows=[]
for sym in SPECS:
 q=pd.read_parquet(O/f'intraday_{sym}.parquet');missing=q.o1001.notna()&q.o1245.isna()
 if missing.any():
  a,_=load(sym);et=a.index.tz_convert('America/New_York');a['date']=et.normalize();a['minute']=et.hour*60+et.minute
  b=a[(a.minute>=765)&a.date.isin(q[missing].index)].groupby('date').first();q['exit_delay']=0.
  for day in q[missing].index:
   if day in b.index and b.loc[day,'instrument_id']==q.loc[day,'o1001_id']:
    q.loc[day,'o1245']=b.loc[day,'open'];q.loc[day,'exit_delay']=b.loc[day,'minute']-765
  q.to_parquet(O/f'intraday_{sym}.parquet')
 rows.append(dict(symbol=sym,missing_before=int(missing.sum()),missing_after=int((q.o1001.notna()&q.o1245.isna()).sum()),max_delay=float(q.get('exit_delay',pd.Series([0])).max())))
 print(rows[-1],flush=True)
pd.DataFrame(rows).to_csv(P/'exit_audit.csv',index=False)
