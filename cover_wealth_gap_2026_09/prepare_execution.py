from pathlib import Path
import pandas as pd,numpy as np,databento as db,json,pandas_market_calendars as mcal
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09';r=json.loads((P/'SPEND_QUOTES.json').read_text());assert r['status']=='complete';parts=[]
for f in r['paths']:
 if not f.endswith('.dbn.zst'):continue
 for x in db.DBNStore.from_file(f).to_df(count=250000):
  t=x.index.tz_convert('America/New_York');m=t.hour*60+t.minute;keep=np.isin(m,[777,778,779,780,957,958,959,960]);z=x.loc[keep].copy()
  if len(z):z['date']=t[keep].tz_localize(None).normalize();z['minute']=m[keep];parts.append(z.reset_index())
pd.concat(parts).to_parquet(P/'execution_snapshots_new.parquet',index=False)
d=pd.read_parquet(P/'trading_status.parquet').reset_index();d=d[d.is_trading.isin(['Y','N'])].sort_values('ts_recv');schedule=mcal.get_calendar('NYSE').schedule('2018-05-01','2026-09-11');allowed=pd.read_parquet(V/'trading_allowed.parquet').copy()
for s,g in d.groupby('symbol'):
 z=g[['ts_recv','is_trading']].drop_duplicates('ts_recv',keep='last');t=pd.DataFrame({'date':schedule.index,'signal_time':schedule.market_close-pd.Timedelta(minutes=2),'fill_time':schedule.market_close-pd.Timedelta(minutes=1)}).reset_index(drop=True);sig=pd.merge_asof(t.sort_values('signal_time'),z,left_on='signal_time',right_on='ts_recv');fill=pd.merge_asof(t.sort_values('fill_time'),z,left_on='fill_time',right_on='ts_recv');allowed[s]=pd.Series((sig.is_trading.eq('Y')&fill.is_trading.eq('Y')).to_numpy(),index=schedule.index)
allowed.to_parquet(P/'trading_allowed.parquet');print('quotes and status prepared',flush=True)
