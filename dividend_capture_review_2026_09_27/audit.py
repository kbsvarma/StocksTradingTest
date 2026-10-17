from pathlib import Path
import pandas as pd,numpy as np,json,hashlib
P=Path(__file__).resolve().parent
manifest=json.loads((P/'manifest.json').read_text())
for row in manifest['files']:
 f=P/'data'/f"{row['ticker']}.csv"
 assert hashlib.sha256(f.read_bytes()).hexdigest()==row['sha256']
 d=pd.read_csv(f,index_col=0,parse_dates=True)
 assert len(d)==row['rows'] and not d.index.duplicated().any()
 assert d.index.max()<=pd.Timestamp('2026-09-25')
checks=[]
for f in sorted(P.glob('trades_*.csv')):
 if 'buyhold' in f.stem:continue
 t=pd.read_csv(f)
 if not len(t):continue
 d=pd.read_csv(P/f.name.replace('trades_','daily_'))
 end=d.equity.iloc[-1];recon=100000+(t.sale_cash+t.dividend-t.buy_cash).sum()
 assert abs(end-recon)<1e-6
 if 'positions' in d:assert d.positions.max()<=4 and d.cash.min()>=-1e-6 and (d.invested<=d.equity+1e-6).all()
 checks.append(dict(strategy=f.stem[7:],trades=len(t),ending_equity=end,reconstructed_equity=recon,error=recon-end))
pd.DataFrame(checks).to_csv(P/'cashflow_reconciliation.csv',index=False)
annual=[];period=[]
for f in sorted(P.glob('daily_*.csv')):
 d=pd.read_csv(f,index_col=0,parse_dates=True)
 for y,g in d.groupby(d.index.year):annual.append(dict(strategy=f.stem[6:],year=y,return_total=(1+g.return_daily).prod()-1))
 for start,end in [('2015','2019'),('2020','2022'),('2023','2026')]:
  g=d.loc[start:end];r=g.return_daily;gross=(1+r).prod();curve=(1+r).cumprod();dd=curve/np.maximum.accumulate(np.r_[1,curve])[1:]-1
  period.append(dict(strategy=f.stem[6:],period=start+'-'+end,annualized_return=gross**(252/len(g))-1,total_return=gross-1,max_drawdown=dd.min()))
pd.DataFrame(annual).to_csv(P/'annual_returns.csv',index=False);pd.DataFrame(period).to_csv(P/'periods.csv',index=False)
print(f"Verified {len(manifest['files'])} raw file hashes and {len(checks)} cash-flow reconciliations; annual/period summaries refreshed.")
