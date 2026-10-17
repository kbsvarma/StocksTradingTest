from pathlib import Path
import pandas as pd,numpy as np,json,sqlite3,hashlib
P=Path(__file__).resolve().parent;R=P.parent
mapped=pd.read_csv(P/'local_mapped_stock_universe.csv');names=set(mapped.symbol)
con=sqlite3.connect(f'file:{R}/.claude/worktrees/compassionate-elion-819afc/sage/db/sage.sqlite?mode=ro',uri=True)
db=pd.read_sql_query('select ticker,date,adj_close,volume from prices',con);con.close();db.ticker=db.ticker.replace({'BRK-B':'BRK.B','BF-B':'BF.B','FB':'META'});names|=set(db.ticker)
# Stock instruments explicitly identifiable in the local daily cache and short intraday cache.
for folder in [R/'stock_overnight_2026_09/data',R/'shares_research/open_fade_2026_07_03/pull_cache']:
 names|={p.stem for p in folder.glob('*.parquet') if not p.stem.startswith('^')}
etfs={'SPY','QQQ','IWM','DIA','TLT','TQQQ','SQQQ','SOXL','SOXS','XLE','XLF','XLK','GLD','SLV','UVXY','VXX','ARKK','SMH','HYG','LQD','EEM','EFA'}
names-=etfs
names.discard("free_pilot")
names={dict(FB="META", **{"BRK-B":"BRK.B","BF-B":"BF.B"}).get(n,n) for n in names}
(P/'all_local_symbols.json').write_text(json.dumps(sorted(names),indent=2))
spy=pd.read_parquet(R/'stock_overnight_2026_09/data/SPY.parquet');cal=pd.to_datetime(spy.index.date);cal=cal[(cal>='2017-01-01')&(cal<='2026-09-11')]
registry=[];candidates={};alternatives=[]
for n in sorted(names):
 opts=[]
 for kind,folder in [('local_daily',R/'stock_overnight_2026_09/data'),('supplemental_yahoo',P/'supplemental_prices'),('local_intraday',R/'shares_research/open_fade_2026_07_03/pull_cache')]:
  path=folder/f'{n}.parquet'
  if not path.exists():continue
  d=pd.read_parquet(path)
  if 'Adj Close' not in d:continue
  q=d['Adj Close'].copy()
  if not isinstance(q,pd.Series):
   alternatives.append(dict(symbol=n,source=kind,path=str(path),status='multi_symbol_container',rows=len(q)));continue
  idx=pd.to_datetime(q.index.date)
  if idx.duplicated().any():
   alternatives.append(dict(symbol=n,source=kind,path=str(path),status='intraday_not_daily_total_return',rows=len(q)));continue
  q.index=idx;q=q.reindex(cal);v=d.Volume.copy() if 'Volume' in d else pd.Series(np.nan,index=d.index);v.index=idx;v=v.reindex(cal)
  nominal=d.Close.copy();nominal.index=idx;nominal=nominal.reindex(cal)
  opts.append((q,v,nominal,kind,str(path),hashlib.sha256(path.read_bytes()).hexdigest()))
 d=db[db.ticker==n]
 if not d.empty:
  q=pd.Series(d.adj_close.to_numpy(),index=pd.to_datetime(d.date)).reindex(cal);v=pd.Series(d.volume.to_numpy(),index=pd.to_datetime(d.date)).reindex(cal)
  opts.append((q,v,q.copy(),'local_sqlite','sage/db/sage.sqlite::prices::'+n,''))
 if not opts:registry.append(dict(symbol=n,status='no_usable_adjusted_daily_history'));continue
 def quality(o):
  q=o[0];valid=np.isfinite(q)&(q>0);disc=valid[(cal>='2021-12-31')&(cal<='2025-12-31')]
  return (bool(disc.all()),int(valid.sum()),int(np.flatnonzero(valid)[-1]) if valid.any() else -1, o[3]=='local_daily')
 q,v,nom,kind,path,sha=max(opts,key=quality);valid=np.isfinite(q)&(q>0);q=q.where(valid)
 idx=np.flatnonzero(valid);runs=np.split(idx,np.flatnonzero(np.diff(idx)>1)+1);best=max(runs,key=len) if len(idx) else []
 row=dict(symbol=n,status='testable' if len(best)>=253 else 'fewer_than_252_continuous_returns',source=kind,path=path,sha256=sha,valid_prices=int(valid.sum()),longest_continuous_prices=len(best),first=str(cal[idx[0]].date()) if len(idx) else '',last=str(cal[idx[-1]].date()) if len(idx) else '',discovery_complete=bool(valid[(cal>='2021-12-31')&(cal<='2025-12-31')].all()),median_discovery_dollar_volume=float((nom*v).loc['2022-01-01':'2025-12-31'].median()),zero_volume_fraction=float((v.loc['2022-01-01':'2025-12-31']==0).mean()))
 registry.append(row)
 if row['status']=='testable':candidates[n]=q
 for o in opts:alternatives.append(dict(symbol=n,source=o[3],path=o[4],status='selected' if o[4]==path else 'alternative_not_spliced',rows=int(o[0].notna().sum())))
reg=pd.DataFrame(registry);reg.to_csv(P/'stock_coverage.csv',index=False);pd.DataFrame(alternatives).to_csv(P/'alternative_sources.csv',index=False)
prices=pd.DataFrame(candidates,index=cal);prices.to_parquet(P/'price_matrix.parquet');np.save(P/'prices.npy',prices.to_numpy(dtype=np.float64).T)
(P/'matrix_symbols.json').write_text(json.dumps(list(prices.columns)));(P/'calendar.json').write_text(json.dumps([str(d.date()) for d in cal]));print('Total local stock symbols',len(names),'testable',len(candidates),'discovery complete',reg.discovery_complete.fillna(False).sum(),flush=True);print(reg.status.value_counts().to_string());print(reg.source.value_counts().to_string())
