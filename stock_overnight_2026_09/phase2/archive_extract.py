from pathlib import Path
import pandas as pd
P=Path(__file__).resolve().parent;R=P.parent.parent
out=[]
for year in range(2018,2027):
 p=R/'opening_leader_2026_07/data'/f'minutes_{year}.parquet';b=pd.read_parquet(p,columns=['ts_event','instrument_id','open','volume','day']);tm=b.ts_event.dt.tz_convert('America/New_York');mins=tm.dt.hour*60+tm.dt.minute
 b=b[mins.isin([570,571,575,585,600])].copy();b['minute']=mins.loc[b.index]
 m=pd.read_parquet(p.parent/f'idmap_{year}.parquet');assert not m.duplicated(['day','instrument_id']).any()
 b=b.merge(m[['day','instrument_id','raw_symbol']],on=['day','instrument_id'],how='left',validate='many_to_one');b['exit']=pd.to_datetime(b.day);b=b.rename(columns={'raw_symbol':'symbol'})
 assert not b.dropna(subset=['symbol']).duplicated(['exit','symbol','minute']).any()
 out.append(b[['exit','symbol','minute','open','volume']]);print(year,len(b),flush=True)
pd.concat(out).to_parquet(P/'archive_opens.parquet',index=False)
