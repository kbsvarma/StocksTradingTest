from pathlib import Path
import pandas as pd
P=Path(__file__).resolve().parent;R=P.parent;l=pd.read_parquet(P/'legs.parquet');l=l[(l['mode']=='exact')&(l.clock=='standard')&l.missing_exit];p=pd.read_parquet(P/'exact_panel.parquet');rows=[]
for year,g in l.groupby(l.day.str[:4]):
 a=pd.read_parquet(R/'opening_leader_2026_07/data'/f'minutes_{year}.parquet');t=a.ts_event.dt.tz_convert('America/New_York');a['minute']=t.dt.hour*60+t.dt.minute
 for r in g.itertuples():
  instrument=p[(p.day==r.day)&(p.symbol==r.symbol)].instrument_id.iloc[0];q=a[(a.day==r.day)&(a.instrument_id==instrument)&a.minute.between(630,635)&(a.volume>0)].sort_values('ts_event')
  if q.empty:rows.append(dict(day=r.day,symbol=r.symbol,status='unresolved'));continue
  z=q.iloc[0];gross=r.side*r.shares*(z.open-r.entry);fee=r.shares*(.06+.00005*(r.entry+z.open));rows.append(dict(day=r.day,symbol=r.symbol,status='first_available',exit_time=str(z.ts_event),exit_price=z.open,marked_net=r.net,recovered_net=gross-fee,delta=gross-fee-r.net))
pd.DataFrame(rows).to_csv(P/'exit_recovery.csv',index=False);print(pd.DataFrame(rows).to_string(index=False))
