from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent
x=pd.concat([pd.read_parquet(P/'liquid_futures.parquet'),pd.read_parquet(P/'secondary_futures.parquet')]);out=P/'liquid_daily';out.mkdir(exist_ok=True)
for symbol,a in x.groupby('symbol'):
 a=a.sort_index();a=a[~a.index.duplicated()];day=a.index.tz_convert('America/New_York').normalize();change=a.instrument_id.ne(a.instrument_id.shift());change.iloc[0]=False
 seam=(a.open-a.close.shift()).where(change,0).fillna(0);moves=a.close.diff()-seam;moves.iloc[0]=a.close.iloc[0]-a.open.iloc[0]
 a['day']=day;d=a.groupby('day').agg(open=('open','first'),close=('close','last'),volume=('volume','sum'),first_id=('instrument_id','first'),last_id=('instrument_id','last'))
 d['gap']=(d.open-d.close.shift()).where(d.first_id.eq(d.last_id.shift()),0).fillna(0);d['clean_move']=moves.groupby(day).sum();d['move_after_open']=d.clean_move-d.gap;d['roll']=change.groupby(day).any();d.to_parquet(out/f'daily_{symbol.split(".")[0]}.parquet')
s=(P/'futures_crp.py').read_text().replace("(D/'data').glob('daily_*.parquet')","(P/'liquid_daily').glob('daily_*.parquet')").replace("P/'futures_crp_scores.csv'","P/'liquid_futures_scores.csv'").replace("P/'futures_crp_coverage.csv'","P/'liquid_futures_coverage.csv'")
exec(compile(s,str(P/'futures_crp.py'),'exec'),dict(__file__=str(P/'futures_crp.py'),__name__='__main__'))
