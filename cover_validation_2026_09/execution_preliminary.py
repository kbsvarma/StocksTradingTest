from pathlib import Path
import pandas as pd,numpy as np,json,pandas_market_calendars as mcal
from cash_ledger import run
P=Path(__file__).resolve().parent;O=P.parent/'cover_exhaustive_2026_09'
x=pd.concat([pd.read_parquet(f) for f in P.glob('execution_snapshots*.parquet')],ignore_index=True).drop_duplicates(['date','minute','symbol'],keep='last');x['symbol']=x.symbol.replace({'BRK/B':'BRK.B','BRK B':'BRK.B'});x=x.drop_duplicates(['date','minute','symbol'],keep='last');original=x.copy()
for target,intervals in json.loads((P/'issuer_alias_intervals.json').read_text()).items():
 parts=[]
 for src,lo,hi in intervals:
  z=original[(original.symbol==src)&(original.date>=lo)&(original.date<hi)].copy();z['symbol']=target;parts.append(z)
 x=pd.concat([x[x.symbol!=target],*parts],ignore_index=True)
schedule=mcal.get_calendar('NYSE').schedule('2018-05-01','2026-09-11');dates=schedule.index;close=schedule.market_close.dt.tz_convert('America/New_York');minutes=pd.Series(close.dt.hour*60+close.dt.minute,index=dates)
x['close_minute']=x.date.map(minutes);sig=x[x.minute==x.close_minute-2].copy();exe=x[x.minute==x.close_minute-1].copy();sig['mid']=(sig.bid_px_00+sig.ask_px_00)/2
S=sig.pivot(index='date',columns='symbol',values='mid').reindex(dates);B=exe.pivot(index='date',columns='symbol',values='bid_px_00').reindex(dates);A=exe.pivot(index='date',columns='symbol',values='ask_px_00').reindex(dates);BS=exe.pivot(index='date',columns='symbol',values='bid_sz_00').reindex(dates);AS=exe.pivot(index='date',columns='symbol',values='ask_sz_00').reindex(dates)
reg=pd.read_csv(O/'stock_coverage.csv').set_index('symbol');raw=pd.read_parquet(P/'vendor_raw_closes.parquet').reindex(dates);div={};mark={};local_fallback={}
for s in S.columns:
 f=P/'expanded_daily'/(s+'.parquet')
 if not f.exists():
  if s not in reg.index or not isinstance(reg.loc[s,'path'],str):continue
  f=Path(reg.loc[s,'path'])
 if not f.exists():continue
 d=pd.read_parquet(f);d.index=pd.to_datetime(d.index.date);d=d[~d.index.duplicated(keep='last')];sp=d['Stock Splits'].replace(0,1);factor=(sp.iloc[::-1].cumprod().iloc[::-1]/sp).reindex(dates)
 for tab in [S,B,A]:tab[s]=tab[s]/factor
 for tab in [BS,AS]:tab[s]=tab[s]*factor
 div[s]=d.Dividends.reindex(dates).fillna(0);v=raw[s]/factor if s in raw else pd.Series(np.nan,index=dates);local_fallback[s]=v.isna()&d.Close.reindex(dates).notna();mark[s]=v.combine_first(d.Close.reindex(dates))
M=pd.DataFrame(mark);D=pd.DataFrame(div);pool=pd.concat([pd.read_csv(P/'verification_pool_extended.csv'),pd.read_csv(P/'verification_pool_expanded.csv'),pd.read_csv(P/'verification_pool_long.csv')]).drop_duplicates(['a','b']);rows=[];curves=[]
for _,pair in pool.iterrows():
 sy=[pair.a,pair.b]
 if not set(sy)<=set(M.columns):continue
 for period,lo,hi in [('vendor_full','2018-05-01','2026-09-11'),('discovery','2021-12-31','2025-12-31'),('confirmation','2025-12-31','2026-09-11')]:
  z=M[sy].loc[lo:hi];good=z.notna().all(axis=1)
  if not good.any():continue
  z=z.loc[good[good].index[0]:good[good].index[-1]];ix=z.index
  if z.isna().any().any() or len(z)<100:continue
  s=S[sy].reindex(ix).to_numpy();b=B[sy].reindex(ix).to_numpy();a=A[sy].reindex(ix).to_numpy();mk=z.to_numpy();dv=D[sy].reindex(ix).to_numpy();years=(ix[-1]-ix[0]).days/365.25
  for lag in [0,30]:
   receipt=ix.searchsorted(ix+pd.Timedelta(days=lag))
   for extra in [0.,.0005,.001]:
    bcap=BS[sy].reindex(ix).to_numpy();acap=AS[sy].reindex(ix).to_numpy();r=run(s,b,a,mk,dv,receipt,extra,True,1000.,bcap,acap);h=run(s,b,a,mk,dv,receipt,extra,False,1000.,bcap,acap);nav,turn,skip,err,trades,minimum,qty,partial,exitflag=r
    assert minimum>=-1e-7
    size=np.where(qty<0,bcap,acap);over=(np.abs(qty)>size+1e-8)&(qty!=0);valid=np.isfinite(s).all(axis=1)&np.isfinite(a).all(axis=1)&np.isfinite(b).all(axis=1)&(b>0).all(axis=1)&(a>=b).all(axis=1)
    if not valid[0] or not valid[-1]:status='invalid_entry_or_exit'
    elif exitflag:status='exit_book_depth_insufficient'
    elif skip/len(ix)>.01:status='quote_gaps_over_1pct'
    elif over.any():status='displayed_size_exceeded'
    else:status='pass'
    rows.append(dict(a=sy[0],b=sy[1],period=period,dividend_lag_days=lag,extra_bps=extra*1e4,start=str(ix[0].date()),end=str(ix[-1].date()),years=years,cagr=(nav[-1]/1000)**(1/years)-1,hold_cagr=(h[0][-1]/1000)**(1/years)-1,terminal_1000=nav[-1],maxdd=(nav/np.maximum.accumulate(np.r_[1000,nav])[1:]-1).min(),annual_log_advantage=np.log(nav[-1]/h[0][-1])/years,skipped_sessions=skip,sessions=len(ix),max_weight_error=err,turnover_dollars=turn,trades=trades,partial_fill_orders=partial,exit_depth_insufficient=exitflag,median_trade_dollars=float(np.median((np.abs(qty)*((b+a)/2))[qty!=0])) if (qty!=0).any() else 0.,size_exceed_orders=int(over.sum()),local_mark_fallbacks=int(sum(local_fallback[t].reindex(ix).sum() for t in sy)),status=status))
    if lag==30 and extra==.001:
     curves.append(pd.DataFrame({'date':ix,'a':sy[0],'b':sy[1],'period':period,'strategy':nav,'hold':h[0]}))
pd.DataFrame(rows).to_csv(P/'execution_preliminary.csv',index=False);pd.concat(curves,ignore_index=True).to_parquet(P/'execution_preliminary_curves.parquet',index=False);print('DONE',len(rows),flush=True)
