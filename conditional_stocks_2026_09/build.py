from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent;R=P.parent;src=R/'opening_leader_2026_07/data';parts=[]
for year in range(2018,2027):
 f=src/f'minutes_{year}.parquet'
 if not f.exists():continue
 a=pd.read_parquet(f);t=a.ts_event.dt.tz_convert('America/New_York');a['minute']=t.dt.hour*60+t.dt.minute;a=a[a.minute.between(570,599)];keys=['day','instrument_id'];g=a.groupby(keys);q=g.agg(high30=('high','max'),low30=('low','min'),volume30=('volume','sum'));cl5=a[a.minute==574].groupby(keys).close.last();cl25=a[a.minute==594].groupby(keys).close.last();q['cl5']=cl5;q['cl25']=cl25;q=q.reset_index();ids=pd.read_parquet(src/f'idmap_{year}.parquet').drop_duplicates(keys);q=q.merge(ids[keys+['raw_symbol']],on=keys,how='left').rename(columns={'raw_symbol':'symbol'});parts.append(q);print(year,flush=True)
f=pd.concat(parts);q=pd.read_parquet(R/'databento_fresh_2026_09/data/stock_morning_panel.parquet');q=q.merge(f,on=['day','instrument_id','symbol'],how='left',validate='one_to_one');q=q.sort_values(['symbol','day']);q['vmed']=q.groupby('symbol').volume30.transform(lambda s:s.rolling(20).median().shift());q['f_r']=q.r;q['f_r5']=q.cl5/q.open-1;q['f_last5']=q.signal/q.cl25-1;q['f_range']=(q.high30-q.low30)/q.open;q['f_location']=(q.signal-q.low30)/(q.high30-q.low30)-.5;q['f_efficiency']=q.r.abs()/q.f_range;q['f_volume']=np.log(q.volume30/q.vmed).clip(-3,3)
for col in ['f_r','f_r5','f_last5']:q[col]=q[col]-q.groupby('day')[col].transform('mean')
for col in ['f_r','f_r5','f_last5']:
 for other in ['f_volume','f_location','f_efficiency']:q[col+'_'+other]=q[col]*q[other]
q['ret']=q.exit/q.entry-1;q.loc[q.missing_exit,'ret']=np.nan;q['label']=q.ret-q.groupby('day').ret.transform('mean');q.to_parquet(P/'panel.parquet');print('complete',len(q),flush=True)
