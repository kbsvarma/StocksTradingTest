from pathlib import Path
import pandas as pd,numpy as np,json
from scipy.optimize import brentq
P=Path(__file__).resolve().parent
T=pd.read_csv(P/'screened_two_lists.csv').drop_duplicates(['a','b']);Q=pd.read_parquet(P/'price_matrix.parquet');reg=pd.read_csv(P/'stock_coverage.csv').set_index('symbol')
listing={}
for file,col in [('nasdaqlisted.txt','Symbol'),('otherlisted.txt','ACT Symbol')]:
 d=pd.read_csv(P/file,sep='|')
 for _,r in d.iterrows():
  if r.get('Test Issue')=='N' and r.get('ETF')=='N':listing[str(r[col]).replace('-','.')]={'name':r['Security Name'],'listed':True}
rows=[];curves=[];annual=[];verify=[];features=[];stock_info=[];events=[]
needed=set(T.a)|set(T.b);raw={}
for n in sorted(needed):
 r=reg.loc[n]
 if r.source!='local_sqlite':
  d=pd.read_parquet(r.path);d.index=pd.to_datetime(d.index.date);raw[n]=d
  # Undo future splits for historical as-traded nominal price diagnostics.
  splits=d['Stock Splits'].replace(0,1) if 'Stock Splits' in d else pd.Series(1.,index=d.index)
  future=splits.iloc[::-1].cumprod().iloc[::-1]/splits;nominal=d.Close*future;v=d.Close*d.Volume
 else:
  d=pd.DataFrame(index=Q.index);nominal=Q[n];v=pd.Series(np.nan,index=Q.index)
 z=nominal.loc['2022-01-01':'2025-12-31']
 stock_info.append(dict(symbol=n,company=listing.get(n,{}).get('name','unverified current listing'),current_exchange_listing=n in listing,median_nominal_discovery_price=float(z.median()),min_nominal_discovery_price=float(z.min()),median_discovery_dollar_volume=float(v.loc['2022-01-01':'2025-12-31'].median()),source=r.source))
def ledger(prices,c,w,rebalance):
 px0=prices.iloc[0].to_numpy();shares=100000/(1+c)*np.array([w,1-w])/px0;previous=100000.;data=[];initial_fee=100000-100000/(1+c)
 for k,(date,px) in enumerate(zip(prices.index[1:],prices.iloc[1:].to_numpy())):
  vals=shares*px;before=vals.sum();trade=np.zeros(2);fee=0.
  if k==len(prices)-2:trade=vals.copy();fee=c*trade.sum();shares*=1-c
  elif rebalance:
   target=np.array([w,1-w]);after=brentq(lambda v:v+c*np.abs(v*target-vals).sum()-before,0,before,xtol=1e-8)
   trade=np.abs(after*target-vals);fee=c*trade.sum();shares=after*target/px
  wealth=float((shares*px).sum());data.append(dict(date=date,wealth=wealth,return_net=wealth/previous-1,trade_a=float(trade[0]),trade_b=float(trade[1]),fee=float(fee),turnover=float(trade.sum()/before)));previous=wealth
 return pd.DataFrame(data),initial_fee
for _,r in T.iterrows():
 a,b=r.a,r.b;pair=a+' / '+b;p=Q[[a,b]];valid=p.notna().all(axis=1);ix=np.flatnonzero(valid);runs=np.split(ix,np.flatnonzero(np.diff(ix)>1)+1);best=max(runs,key=len);full=p.iloc[best]
 segments={'full':full,'evaluation':p.loc['2021-12-31':r.confirmation_end],'discovery':p.loc['2021-12-31':'2025-12-31'],'confirmation':p.loc['2025-12-31':r.confirmation_end]}
 for period,prices in segments.items():
  assert prices.notna().all().all();years=(prices.index[-1]-prices.index[0]).days/365.25
  for bps in [0,5,10,25,50]:
   for model,w,rebal in [('daily_50_50',.5,True),('hold_50_50',.5,False),('a_hold',1.,False),('b_hold',0.,False)]:
    d,initialfee=ledger(prices,bps/10000,w,rebal);wealth=np.r_[100000,d.wealth.to_numpy()];dd=wealth/np.maximum.accumulate(wealth)-1;terminal=wealth[-1];cagr=(terminal/100000)**(1/years)-1
    row=dict(pair=pair,a=a,b=b,period=period,strategy=model,cost_bps=bps,start=str(prices.index[0].date()),end=str(prices.index[-1].date()),days=len(d),years=years,terminal_wealth=terminal,cagr=cagr,total_return=terminal/100000-1,max_drawdown=float(dd.min()),annual_turnover=(1+d.turnover.sum())/years,cost_dollars=initialfee+d.fee.sum())
    if period!='evaluation' and model=='daily_50_50' and bps in [0,5,10]:
     expected=r.get(period+f'_cagr_{bps}bps',np.nan);err=abs(cagr-expected);assert err<1e-8;verify.append(dict(pair=pair,period=period,cost_bps=bps,cagr_error=err))
    if period!='evaluation' and model in ['hold_50_50','a_hold','b_hold'] and bps==5:
     key={'hold_50_50':'hold','a_hold':'a','b_hold':'b'}[model];expected=r.get(period+'_'+key+'_cagr_5bps',np.nan);err=abs(cagr-expected);assert err<1e-8;verify.append(dict(pair=pair,period=period,cost_bps=bps,strategy=model,cagr_error=err))
    if model=='daily_50_50' and bps==5:
     participation=[]
     for n,tradecol in [(a,'trade_a'),(b,'trade_b')]:
      if n in raw:
       dv=(raw[n].Close*raw[n].Volume).reindex(d.date)
       with np.errstate(divide='ignore',invalid='ignore'):v=d[tradecol].to_numpy()/dv.to_numpy()
       participation.extend(v[~np.isnan(v)].tolist())
     row['max_rebalance_or_exit_dollar_volume_fraction']=max(participation) if participation else np.nan
    rows.append(row)
    if bps in [5,10]:
     d['pair']=pair;d['period']=period;d['strategy']=model;d['cost_bps']=bps;curves.append(d)
     previous=100000
     for year,z in d.groupby(d.date.dt.year):
      terminal=z.wealth.iloc[-1];annual.append(dict(pair=pair,period=period,strategy=model,cost_bps=bps,year=int(year),return_net=float(terminal/previous-1)));previous=terminal
  g=(prices/prices.shift()).iloc[1:];log=np.log(g);bonus=np.log(g.mean(axis=1))-log.mean(axis=1);share=float(bonus.nlargest(5).sum()/bonus.sum())
  features.append(dict(pair=pair,period=period,vol_a=float(log[a].std()*np.sqrt(252)),vol_b=float(log[b].std()*np.sqrt(252)),correlation=float(log[a].corr(log[b])),weighted_stock_log_growth=float(log.mean(axis=1).sum()/years),diversification_log_bonus=float(bonus.sum()/years),top5_days_share_of_diversification_bonus=share))
 print(pair,'verified',flush=True)
pd.DataFrame(rows).to_csv(P/'shortlist_summary.csv',index=False);pd.concat(curves,ignore_index=True).to_parquet(P/'shortlist_daily.parquet',index=False);pd.DataFrame(annual).to_csv(P/'shortlist_annual.csv',index=False);pd.DataFrame(verify).to_csv(P/'shortlist_verification.csv',index=False);pd.DataFrame(features).to_csv(P/'shortlist_features.csv',index=False);pd.DataFrame(stock_info).to_csv(P/'shortlist_companies.csv',index=False)
