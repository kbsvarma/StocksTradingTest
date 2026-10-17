from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent;out=P/'mapped_local';out.mkdir(exist_ok=True);summary=[];all_symbols=set()
for y in range(2018,2027):
 dest=out/f'raw_{y}.parquet';d=pd.read_parquet(P.parent/f'opening_leader_2026_07/data/daily_{y}.parquet');d=d[['instrument_id','close','volume']].reset_index();d['day']=pd.to_datetime(d.ts_event,utc=True).dt.tz_localize(None).dt.normalize();d=d.drop(columns='ts_event');d.instrument_id=d.instrument_id.astype('int64')
 maps=pd.concat([pd.read_parquet(f) for f in sorted((P/'recovered_maps').glob(f'{y}_*.parquet'))],ignore_index=True);maps.start=pd.to_datetime(maps.start);maps.end=pd.to_datetime(maps.end);maps.instrument_id=maps.instrument_id.astype('int64');maps=maps.sort_values('start')
 r=pd.merge_asof(d.sort_values('day'),maps,left_on='day',right_on='start',by='instrument_id');r.loc[r.day>=r.end,'symbol']=None;r=r.drop(columns=['start','end']);r.to_parquet(dest,index=False);sy=set(r.symbol.dropna());all_symbols|=sy;summary.append({'year':y,'rows':len(r),'mapped_rows':int(r.symbol.notna().sum()),'symbols':len(sy)});print(summary[-1],flush=True)
(P/'RECOVERED_SYMBOLS.json').write_text(json.dumps(sorted(all_symbols)));pd.DataFrame(summary).to_csv(P/'raw_mapping_coverage.csv',index=False);print('ALL SYMBOLS',len(all_symbols),flush=True)
