from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent;d=pd.read_csv(P/'execution_results.csv');d.to_csv(P/'execution_bbo_only.csv',index=False);book=pd.read_parquet(P/'terminal_depth.parquet');book['symbol']=book.symbol.replace({'BRK/B':'BRK.B','BRK B':'BRK.B'});aliases=json.loads((P/'issuer_alias_intervals.json').read_text());raw=book.copy()
for target,intervals in aliases.items():
 parts=[]
 for src,lo,hi in intervals:
  z=raw[(raw.symbol==src)&(raw.date>=lo)&(raw.date<hi)].copy();z['symbol']=target;parts.append(z)
 book=pd.concat([book[book.symbol!=target],*parts],ignore_index=True)
book=book.set_index(['date','symbol']);events=[];updated=[]
def sweep(r,side,q):
 capacity=r['terminal_capacity_'+side];base=r['terminal_bid_'+side];factor=r['terminal_split_factor_'+side]
 if q<=capacity+1e-8:return 0.,True,0.
 key=(r.end,r[side])
 if key not in book.index:return 0.,False,np.nan
 z=book.loc[key];top=z.bid_px_00/factor;discrepancy=top/base-1
 if abs(discrepancy)>.0001:return 0.,False,discrepancy
 remaining=q;proceeds=0.
 for level in range(10):
  price=z[f'bid_px_{level:02d}']/factor;size=z[f'bid_sz_{level:02d}']*factor
  if not np.isfinite(price+size) or price<=0 or size<=0:continue
  amount=min(remaining,size);proceeds+=amount*price;remaining-=amount
  if remaining<1e-8:break
 return max(0.,q*base-proceeds)*(1-r.extra_bps/10000),remaining<1e-8,discrepancy
for _,r in d.iterrows():
 extra=0.;holdextra=0.;valid=True;needed=False;maxdiff=0.
 for side in ['a','b']:
  q=r['terminal_shares_'+side];hq=r['hold_terminal_shares_'+side]
  if max(q,hq)>r['terminal_capacity_'+side]+1e-8:needed=True
  loss,ok,diff=sweep(r,side,q);hloss,hok,hdiff=sweep(r,side,hq);extra+=loss;holdextra+=hloss;valid &= ok and hok
  if np.isfinite(diff):maxdiff=max(maxdiff,abs(diff))
 r['terminal_depth_needed']=needed;r['terminal_depth_verified']=valid;r['terminal_depth_extra_cost']=extra if valid else np.nan;r['hold_terminal_depth_extra_cost']=holdextra if valid else np.nan;r['terminal_depth_top_quote_difference']=maxdiff
 if valid:
  r.terminal_1000-=extra;r.hold_terminal_1000-=holdextra;r.cagr=(r.terminal_1000/1000)**(1/r.years)-1;r.hold_cagr=(r.hold_terminal_1000/1000)**(1/r.years)-1;r.annual_log_advantage=np.log(r.terminal_1000/r.hold_terminal_1000)/r.years;r.maxdd=min(r.maxdd,r.terminal_1000/max(r.pre_terminal_peak_nav,r.terminal_1000)-1)
 if r.status=='invalid_entry_or_exit':pass
 elif not valid:r.status='exit_book_depth_insufficient'
 elif r.skipped_sessions/r.sessions>.01:r.status='quote_gaps_over_1pct'
 elif r.size_exceed_orders:r.status='displayed_size_exceeded'
 elif r.partial_fill_orders:r.status='pass_with_partial_fills'
 elif needed:r.status='pass_with_depth_sweep'
 else:r.status='pass'
 updated.append(r)
z=pd.DataFrame(updated);z.to_csv(P/'execution_results.csv',index=False)
curves=pd.read_parquet(P/'execution_curves.parquet');curves.to_parquet(P/'execution_bbo_only_curves.parquet',index=False)
for _,r in z[(z.extra_bps==10)&(z.dividend_lag_days==30)&z.terminal_depth_verified&z.terminal_depth_needed].iterrows():
 ix=(curves.a==r.a)&(curves.b==r.b)&(curves.period==r.period)&(curves.date==r.end);curves.loc[ix,'strategy']=r.terminal_1000;curves.loc[ix,'hold']=r.hold_terminal_1000
curves.to_parquet(P/'execution_curves.parquet',index=False);print('depth needed',int(z.terminal_depth_needed.sum()),'unresolved',int((~z.terminal_depth_verified).sum()),'max added strategy cost',z.terminal_depth_extra_cost.max(),flush=True)
