from pathlib import Path
import pandas as pd,numpy as np,json,pandas_market_calendars as mcal
P=Path(__file__).resolve().parent;d=pd.concat([pd.read_parquet(f) for f in [P/'trading_status.parquet',P/'trading_status_spac.parquet']]).reset_index();d['symbol']=d.symbol.replace({'BRK/B':'BRK.B','BRK B':'BRK.B'});d['date']=d.ts_recv.dt.tz_convert('America/New_York').dt.tz_localize(None).dt.normalize();raw=d.copy()
for target,intervals in json.loads((P/'issuer_alias_intervals.json').read_text()).items():
 parts=[]
 for src,lo,hi in intervals:
  z=raw[(raw.symbol==src)&(raw.date>=lo)&(raw.date<hi)].copy();z['symbol']=target;parts.append(z)
 d=pd.concat([d[d.symbol!=target],*parts],ignore_index=True)
d=d[d.is_trading.isin(['Y','N'])].sort_values('ts_recv');schedule=mcal.get_calendar('NYSE').schedule('2018-05-01','2026-09-11');pool=pd.concat([pd.read_csv(P/f) for f in ['verification_pool_extended.csv','verification_pool_expanded.csv','verification_pool_long.csv']]);sy=sorted(set(pool.a)|set(pool.b));out={};details=[]
for s in sy:
 z=d[d.symbol==s][['ts_recv','is_trading','action','reason']].drop_duplicates('ts_recv',keep='last')
 t=pd.DataFrame({'date':schedule.index,'signal_time':schedule.market_close-pd.Timedelta(minutes=2),'fill_time':schedule.market_close-pd.Timedelta(minutes=1)}).reset_index(drop=True)
 if z.empty:out[s]=pd.Series(False,index=schedule.index);continue
 sig=pd.merge_asof(t.sort_values('signal_time'),z,left_on='signal_time',right_on='ts_recv');fill=pd.merge_asof(t.sort_values('fill_time'),z,left_on='fill_time',right_on='ts_recv');valid=sig.is_trading.eq('Y')&fill.is_trading.eq('Y');out[s]=pd.Series(valid.to_numpy(),index=schedule.index)
 bad=t.loc[~valid].copy();bad['symbol']=s;bad['signal_state']=sig.loc[~valid,'is_trading'];bad['fill_state']=fill.loc[~valid,'is_trading'];bad['fill_action']=fill.loc[~valid,'action'];bad['fill_reason']=fill.loc[~valid,'reason'];details.append(bad)
pd.DataFrame(out).to_parquet(P/'trading_allowed.parquet');pd.concat(details,ignore_index=True).to_csv(P/'halted_or_unknown_sessions.csv',index=False);print('DONE',len(out),flush=True)
