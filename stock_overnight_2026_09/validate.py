from pathlib import Path
import pandas as pd,numpy as np,json,hashlib
from scipy.stats import linregress
P=Path(__file__).resolve().parent
s=pd.read_csv(P/'selection.csv');tr=pd.read_parquet(P/'trades.parquet');daily=pd.read_csv(P/'daily.csv',parse_dates=['entry','exit'])
spy=pd.read_parquet(P/'data/SPY.parquet');calendar=pd.to_datetime(spy.index.date)
for r in s.sample(100,random_state=123).itertuples():
 b=pd.read_parquet(P/'data'/f'{"META" if r.symbol=="FB" else r.symbol}.parquet');b.index=pd.to_datetime(b.index.date);b=b.reindex(calendar)
 before=calendar[calendar<pd.Timestamp(r.month+'-01')][-252:]
 x=np.log((b.Close+b.Dividends.fillna(0))/b.Close.shift()).loc[before];y=np.log((b.Open+b.Dividends.fillna(0))/b.Close.shift()).loc[before];valid=x.notna()&y.notna()
 fit=linregress(x[valid],y[valid]);assert abs(fit.intercept-r.alpha)<1e-12;assert abs(fit.intercept/fit.intercept_stderr-r.tstat)<1e-8
for r in tr[~tr.missing_exit].sample(1000,random_state=123).itertuples():
 b=pd.read_parquet(P/'data'/f'{"META" if r.symbol=="FB" else r.symbol}.parquet');b.index=pd.to_datetime(b.index.date)
 assert abs((b.loc[r.exit,'Open']+b.loc[r.exit,'Dividends'])/b.loc[r.entry,'Close']-1-r.ret)<1e-12
 assert calendar[calendar.get_loc(r.entry)+1]==r.exit
 assert r.symbol=='SPY' or ((s.month==r.entry.strftime('%Y-%m'))&(s.symbol==r.symbol)).any()
assert not daily.duplicated(['entry','strategy']).any()
assert (daily.weight<=1+1e-10).all()
assert tr.groupby(['entry','strategy']).weight.sum().max()<1+1e-10
summary=pd.read_csv(P/'summary.csv')
for _,g in summary.groupby(['strategy','period']):assert g.sort_values('cost_bps').cagr.is_monotonic_decreasing
hashes={str(f.relative_to(P)):hashlib.sha256(f.read_bytes()).hexdigest() for f in P.rglob('*') if f.is_file() and f.suffix in ['.py','.parquet']}
(P/'data_hashes.json').write_text(json.dumps(hashes,indent=2))
(P/'VALIDATION.md').write_text('Independent scipy.linregress checks: 100 monthly fits passed. Independently recomputed 1,000 sampled stock overnight returns and next-session alignment. Monthly eligibility, portfolio weight bounds, unique portfolio dates, and cost monotonicity passed. These validate calculation consistency, not vendor price accuracy or executability. SHA-256 hashes saved in data_hashes.json.\n')
print('Validation passed')
