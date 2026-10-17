from pathlib import Path
import pandas as pd,numpy as np,json
from db_access import download
P=Path(__file__).resolve().parent;d=pd.read_csv(P/'execution_results.csv');aliases=json.loads((P/'issuer_alias_intervals.json').read_text());requests={}
for _,r in d.iterrows():
 for side in ['a','b']:
  q=max(r['terminal_shares_'+side],r['hold_terminal_shares_'+side]);cap=r['terminal_capacity_'+side]
  if q<=cap+1e-8:continue
  symbol=r[side];raw=symbol
  for src,lo,hi in aliases.get(symbol,[]):
   if lo<=r.end<hi:raw=src
  if raw=='BRK.B':raw='BRK/B'
  requests.setdefault(r.end,set()).add(raw)
rows=[]
for date,symbols in sorted(requests.items()):
 start=pd.Timestamp(date+' 15:58',tz='America/New_York').tz_convert('UTC').isoformat();end=pd.Timestamp(date+' 15:59',tz='America/New_York').tz_convert('UTC').isoformat();name='terminal_depth_'+date;data=download(name,dict(dataset='XNAS.ITCH',schema='mbp-10',symbols=sorted(symbols),start=start,end=end),ledger_name='SPEND_TERMINAL_DEPTH.json');x=data.to_df().reset_index();x=x.sort_values('ts_recv').groupby('symbol',sort=False).tail(1);x['date']=date;rows.append(x);print('DEPTH',date,len(symbols),len(x),flush=True)
pd.concat(rows,ignore_index=True).to_parquet(P/'terminal_depth.parquet',index=False);print('DONE',flush=True)
