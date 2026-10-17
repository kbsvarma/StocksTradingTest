from pathlib import Path
import pandas as pd
P=Path(__file__).resolve().parent;S=P.parent/'opening_leader_2026_07/data';u=pd.read_parquet(S/'universe.parquet');u=u[u.mega];parts=[]
for year in range(2018,2027):
 f=S/f'minutes_{year}.parquet'
 if not f.exists():continue
 a=pd.read_parquet(f);lt=a.ts_event.dt.tz_convert('America/New_York');a['minute']=lt.dt.hour*60+lt.dt.minute;a=a[a.minute.isin([570,599,601,602,630,631,632,633,634,635])];keys=['day','instrument_id'];out=[]
 for minute,field,name in [(570,'open','open'),(599,'close','signal'),(601,'open','entry'),(602,'open','entry_delay'),(630,'open','exit'),(631,'open','exit_delay')]:
  b=a[(a.minute==minute)&(a.volume>0)].sort_values('ts_event').groupby(keys)[field].first().rename(name);out.append(b)
 out.append(a[(a.minute.between(630,635))&(a.volume>0)].sort_values('ts_event').groupby(keys).open.first().rename('exit_recovered'));q=pd.concat(out,axis=1).reset_index();ids=pd.read_parquet(S/f'idmap_{year}.parquet').drop_duplicates(keys);q=q.merge(ids[keys+['raw_symbol']],on=keys,validate='one_to_one').rename(columns={'raw_symbol':'symbol'});q['month']=q.day.str[:7];q=q.merge(u,on=['month','symbol'],validate='many_to_one');q['r']=q.signal/q.open-1;parts.append(q);print(year,len(q),flush=True)
a=pd.concat(parts);assert not a.duplicated(['day','symbol']).any();a.to_parquet(P/'exact_panel.parquet')
