from pathlib import Path
import pandas as pd,numpy as np,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'cover_validation_2026_09'))
from db_access import client,safe_error
P=Path(__file__).resolve().parent;u=pd.read_parquet(P.parent/'trend_replication_2026_07/data/etf/SPY_1m.parquet');et=u.index.tz_convert('America/New_York');u=u[(et.hour==9)&(et.minute==59)].copy();u['date']=u.index.tz_convert('America/New_York').tz_localize(None).normalize();u=u[u.date<'2026-05-01'];plans=[]
for quarter,g in u.groupby(u.date.dt.to_period('Q')):
 syms=[]
 for r in g.itertuples():
  k=round(r.close);exp=r.date
  import exchange_calendars as xc
  cal=xc.get_calendar('XNYS')
  friday=exp+pd.Timedelta(days=((4-exp.dayofweek)%7 or 7))
  while not cal.is_session(friday):friday-=pd.Timedelta(days=1)
  for maturity in sorted(set([exp,friday])):
   for right in ['C','P']:syms.append(f'SPY   {maturity:%y%m%d}{right}{k*1000:08d}')
 params=dict(dataset='OPRA.PILLAR',schema='cbbo-1m',symbols=sorted(set(syms)),stype_in='raw_symbol',start=str(quarter.start_time.date()),end=str(min(quarter.end_time+pd.Timedelta(days=1),pd.Timestamp('2026-05-01')).date()))
 try:cost=float(client.metadata.get_cost(**params));plans.append(dict(name='spy_atm_'+str(quarter),params=params,quote=cost));print(quarter,cost,flush=True)
 except Exception as e:plans.append(dict(name=str(quarter),error=safe_error(e)));print('ERROR',quarter,safe_error(e),flush=True)
(P/'spy_missing_plan.json').write_text(json.dumps(plans,indent=2));print('TOTAL',sum(x.get('quote',0) for x in plans),flush=True)
