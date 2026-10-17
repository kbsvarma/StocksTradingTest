from pathlib import Path
import pandas as pd,numpy as np
P=Path(__file__).resolve().parent
tr=pd.read_parquet(P/'trades.parquet');d=pd.read_csv(P/'daily.csv',parse_dates=['entry','exit']);c=pd.read_csv(P/'coverage.csv');c['year']=c.month.str[:4]
ct=pd.crosstab(c.year,c.reason);ct['eligible_pct']=ct.eligible/ct.sum(axis=1)*100;ct.to_csv(P/'coverage_annual.csv');print(ct.to_string())
rows=[]
for (s,period),start in [((s,p),st) for s in ['OBG','TopDecile'] for p,st in [('All','2018'),('2022+','2022'),('2025+','2025')]]:
 q=d[(d.strategy==s)&(d.entry>=start)].copy();r=q.gross.to_numpy();w=q.weight.to_numpy()
 lo,hi=0,100
 for _ in range(60):
  mid=(lo+hi)/2
  if np.log1p(r-w*mid/1e4).sum()>0:lo=mid
  else:hi=mid
 worst=q.loc[q.gross.idxmin()];best=q.nlargest(5,'gross').index
 rr=q.gross-q.weight*.0003;rr.loc[best]=0
 rows.append(dict(strategy=s,period=period,compound_breakeven_cost_bps=lo,worst_gross_night_pct=worst.gross*100,worst_entry=str(worst.entry.date()),without_best5_cagr_3bps=(np.prod(1+rr)**(252/len(rr))-1)*100))
pd.DataFrame(rows).to_csv(P/'diagnostics.csv',index=False);print(pd.DataFrame(rows).to_string(index=False))
# Audit against independent vendor adjusted-close total return; discrepancies identify corporate actions.
audit=[]
for f in (P/'data').glob('*.parquet'):
 if f.stem=='free_pilot':continue
 b=pd.read_parquet(f);b.index=pd.to_datetime(b.index.date)
 raw=(b.Close+b.Dividends)/b.Close.shift()-1;adj=b['Adj Close']/b['Adj Close'].shift()-1
 gap=(raw-adj).abs()
 for day in gap[gap>.005].index:
  audit.append(dict(symbol=f.stem,day=str(day.date()),gap=float(gap.loc[day]),raw=float(raw.loc[day]),adj=float(adj.loc[day])))
pd.DataFrame(audit).to_csv(P/'corporate_action_audit.csv',index=False)
print('corporate action mismatches >50bps',len(audit));print(pd.DataFrame(audit).sort_values('gap',ascending=False).head(15).to_string(index=False))
# Top contributors in recent period, arithmetic allocation-weighted (not additive compounded attribution).
z=tr[(tr.strategy=='TopDecile')&(tr.entry>='2025-01-01')].copy();z['contribution']=z.weight*z.ret
print('Recent contribution');print(z.groupby('symbol').contribution.sum().sort_values(ascending=False).head(15).to_string())
print('Selected missing exits');print(tr[tr.missing_exit].to_string(index=False))
