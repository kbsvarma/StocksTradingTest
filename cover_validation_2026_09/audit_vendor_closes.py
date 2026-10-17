from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent;O=P.parent/'cover_exhaustive_2026_09';reg=pd.read_csv(O/'stock_coverage.csv').set_index('symbol');files=list((P/'parsed_closes').glob('*.parquet'));v=pd.concat([pd.read_parquet(f) for f in files],ignore_index=True)
v['source_job'] = np.repeat([f.stem for f in files],[len(pd.read_parquet(f,columns=['price'])) for f in files])
g=v.groupby(['date','symbol']).price.agg(['min','max','count']);g['relative_spread']=g['max']/g['min']-1;g[g['relative_spread']>.0001].to_csv(P/'cross_venue_close_conflicts.csv')
v['symbol']=v.symbol.replace({'BRK/B':'BRK.B','BRK B':'BRK.B'})
v['primary_priority']=((v.symbol=='CBOE')&v.source_job.eq('cboe_primary_statistics')).astype(int)
v=v.sort_values(['primary_priority','ts_event']).drop_duplicates(['date','symbol'],keep='last')
v.to_parquet(P/'vendor_unmapped_close_records.parquet',index=False)
original=v.copy(); aliases=json.loads((P/'issuer_alias_intervals.json').read_text())
for target,intervals in aliases.items():
 pieces=[]
 for source,start,end in intervals:
  part=original[(original.symbol==source)&(original.date>=start)&(original.date<end)].copy();part['raw_symbol']=source;part['symbol']=target;pieces.append(part)
 v=pd.concat([v[v.symbol!=target],*pieces],ignore_index=True)
rows=[];errors=[];adjusted={};prices={}

for sym,z in v.groupby('symbol'):
 path=P/'expanded_daily'/f'{sym}.parquet'
 if not path.exists():
  if sym not in reg.index or not isinstance(reg.loc[sym,'path'],str) or '::' in reg.loc[sym,'path']:continue
  path=Path(reg.loc[sym,'path'])
 if not path.exists():continue
 try:
  d=pd.read_parquet(path);d.index=pd.to_datetime(d.index.date);d=d[~d.index.duplicated(keep='last')];sp=d['Stock Splits'].replace(0,1);future=sp.iloc[::-1].cumprod().iloc[::-1]/sp;nom=d.Close*future;z=z.set_index('date');cmp=pd.DataFrame({'official':z.price,'local_nominal':nom}).dropna();diff=cmp.official/cmp.local_nominal-1;dis=diff.loc['2021-12-31':'2025-12-31'];localdisc=d.loc['2021-12-31':'2025-12-31'];rows.append({'symbol':sym,'vendor_sessions':len(z),'compared':len(cmp),'over_10bp':int((diff.abs()>.001).sum()),'over_100bp':int((diff.abs()>.01).sum()),'max_difference':float(diff.abs().max()),'discovery_compared':len(dis),'discovery_coverage':len(dis)/len(localdisc) if len(localdisc) else np.nan,'discovery_over_10bp':int((dis.abs()>.001).sum()),'discovery_over_100bp':int((dis.abs()>.01).sum())})
  flagged=cmp.loc[diff.abs()>.001].copy();flagged['symbol']=sym;flagged['relative_difference']=diff.loc[flagged.index];errors.append(flagged.reset_index(names='date'))
  prices[sym]=z.price;adjusted[sym]=(z.price/future*(d['Adj Close']/d.Close)).dropna()
 except Exception as e:rows.append({'symbol':sym,'error':str(e)[:200]})
pd.DataFrame(rows).to_csv(P/'all_stock_price_audit.csv',index=False);pd.concat(errors,ignore_index=True).to_parquet(P/'vendor_price_flags.parquet',index=False);pd.DataFrame(adjusted).to_parquet(P/'vendor_hybrid_adjusted.parquet');pd.DataFrame(prices).to_parquet(P/'vendor_raw_closes.parquet');print('AUDITED',len(rows),'parsedfiles',len(files),flush=True)
