from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent;R=P.parent
reg=pd.read_csv(P/'stock_coverage.csv');prices=pd.read_parquet(P/'price_matrix.parquet');rows=[];events=[]
panel=pd.read_parquet(R/'volatile_direction_2026_07/data/pit_panel_2018_2026.parquet');panel.raw_symbol=panel.raw_symbol.replace({'FB':'META','BRK-B':'BRK.B'});rawgroups=dict(tuple(panel.groupby('raw_symbol')))
for _,r in reg[reg.status=='testable'].iterrows():
 n=r.symbol;q=prices[n];g=q/q.shift();valid=q.notna()&q.shift().notna();diff=pd.Series(np.nan,index=q.index);splits=pd.Series(0.,index=q.index)
 if r.source!='local_sqlite':
  d=pd.read_parquet(r.path);d.index=pd.to_datetime(d.index.date);d=d.reindex(q.index)
  if {'Close','Dividends'}<=set(d.columns):diff=g-(d.Close+d.Dividends)/d.Close.shift()
  if 'Stock Splits' in d:splits=d['Stock Splits'].fillna(0)
 for day in q.index[(valid&(abs(g-1)>.20))|(abs(diff)>.005)]:
  events.append(dict(symbol=n,date=str(day.date()),adjusted_return=float(g.loc[day]-1),close_dividend_discrepancy=float(diff.loc[day]),split=float(splits.loc[day])))
 raw=rawgroups.get(n);discrep=0;comparisons=0;segments=0
 if raw is not None:
  raw=raw.sort_values('day').drop_duplicates('day',keep='last');raw.day=pd.to_datetime(raw.day);raw=raw.set_index('day');segments=raw.history_segment.nunique()
  qq=q.reindex(raw.index);same=raw.history_segment==raw.history_segment.shift();a=np.log(raw.adj_close/raw.adj_close.shift());b=np.log(qq/qq.shift());vv=same&a.notna()&b.notna()&np.isfinite(a)&np.isfinite(b)
  comparisons=int(vv.sum());discrep=int(((abs(a-b)>.10)&vv).sum())
 rows.append(dict(symbol=n,source=r.source,jumps_over_20pct=int(((abs(g-1)>.2)&valid).sum()),adjustment_discrepancies_over_50bps=int((abs(diff)>.005).sum()),raw_return_comparisons=comparisons,raw_log_discrepancies_over_10pct=discrep,raw_history_segments=segments,max_abs_daily_return=float(abs(g[valid]-1).max())))
pd.DataFrame(rows).to_csv(P/'stock_price_audit.csv',index=False);pd.DataFrame(events).to_csv(P/'price_events.csv',index=False);print('Audited',len(rows),'stocks;',len(events),'flagged events',flush=True)
