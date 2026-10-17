from pathlib import Path
import pandas as pd,numpy as np,json,hashlib
P=Path(__file__).resolve().parent
T='O VZ PM MO XOM CVX PEP KO JNJ PG SO DUK'.split()
D={s:pd.read_csv(P/'data'/f'{s}.csv',index_col=0,parse_dates=True) for s in T}
IDX=D[T[0]].index
assert all(x.index.equals(IDX) for x in D.values())
START=IDX.searchsorted(pd.Timestamp('2015-01-01')); END=len(IDX)-1
DATES=IDX[START:]; YEARS=(DATES[-1]-DATES[0]).days/365.25
COST=.0005
# Provider Close and cash distributions share the same split-adjusted unit basis.
# O's 1.032 synthetic split represents its Orion spin-off, not a normal stock split.
# Core results retain this provider approximation; no-O results are supplied.
for s,d in D.items():
 assert (d.Close>0).all() and not d.Close.isna().any()
 d['ttm']=d.Dividends.rolling('365D',closed='left').sum().fillna(0)/d.Close
 d['audit_residual']=d['Adj Close'].pct_change()-((d.Close+d.Dividends)/d.Close.shift()-1)
 pd.DataFrame(d.loc[(d.Dividends>0)|(d['Stock Splits']!=0),['Dividends','Stock Splits','Close','audit_residual']]).to_csv(P/f'actions_{s}.csv')

rows=[]
for before in (1,5):
 for after in (0,5,10):
  for s,d in D.items():
   for k in np.flatnonzero(d.Dividends.to_numpy()>0):
    a,b=k-before,k+after
    if a<START or b>END:continue
    buy,sell=d.Close.iloc[a],d.Close.iloc[b]
    div=d.Dividends.iloc[a+1:b+1].sum()
    net=(sell*(1-COST)+div)/(buy*(1+COST))-1
    rows.append(dict(ticker=s,before=before,after=after,entry=str(IDX[a].date()),ex_date=str(IDX[k].date()),exit=str(IDX[b].date()),a=a,k=k,b=b,year=IDX[k].year,entry_price=buy,exit_price=sell,dividend_per_share=div,dividend_yield=div/buy,price_return=sell/buy-1,gross_return=(sell+div)/buy-1,net_return=net,ttm_yield=d.ttm.iloc[a],adjusted_return=d['Adj Close'].iloc[b]/d['Adj Close'].iloc[a]-1,spinoff_crossing=bool(s=='O' and IDX[a]<pd.Timestamp('2021-11-15')<=IDX[b])))
E=pd.DataFrame(rows)
# Matched reference: every no-dividend window of the same duration, ticker and entry year.
# This is a descriptive reference (overlapping controls), not a randomized trial.
control={}
for s,d in D.items():
 for length in (1,5,6,10,11,15):
  for high in (False,True):
   vals=[]
   for a in range(START,END-length+1):
    b=a+length
    if d.Dividends.iloc[a+1:b+1].sum()!=0:continue
    if high and d.ttm.iloc[a]<.03:continue
    if s=='O' and IDX[a]<pd.Timestamp('2021-11-15')<=IDX[b]:continue
    vals.append((IDX[a].year,d.Close.iloc[b]*(1-COST)/(d.Close.iloc[a]*(1+COST))-1))
   for y in range(2015,2027):
    v=[r for yy,r in vals if yy==y]
    control[(s,length,high,y)]=np.mean(v) if v else np.nan
for high in (False,True):
 E['control_high' if high else 'control_all']=[control.get((r.ticker,r.before+r.after,high,pd.Timestamp(r.entry).year),np.nan) for r in E.itertuples()]
E.to_csv(P/'events.csv',index=False)

def bootstrap(g,column):
 g=g.dropna(subset=[column]); z=g.groupby('year')[column].agg(['sum','count']).to_numpy()
 rng=np.random.default_rng(4291); take=rng.integers(0,len(z),size=(10000,len(z)))
 vals=z[take,0].sum(axis=1)/z[take,1].sum(axis=1)
 return np.quantile(vals,[.025,.975]).tolist()

def esummary(g):
 r=g.net_return
 return dict(events=len(g),mean=r.mean(),median=r.median(),win_rate=(r>0).mean(),mean_dividend=g.dividend_yield.mean(),mean_price=g.price_return.mean(),mean_win=r[r>0].mean(),mean_loss=r[r<=0].mean(),worst=r.min(),best=r.max(),profit_factor=r[r>0].sum()/(-r[r<0].sum()),ci_low=bootstrap(g,'net_return')[0],ci_high=bootstrap(g,'net_return')[1])
summary=[]; stocks=[]
for high in (False,True):
 for (bef,aft),g0 in E.groupby(['before','after']):
  g=g0[g0.ttm_yield>=.03].copy() if high else g0.copy()
  col='control_high' if high else 'control_all';g['excess']=g.net_return-g[col]
  row=dict(high_yield=high,before=int(bef),after=int(aft),**esummary(g),control_mean=g[col].mean(),excess=g.excess.mean(),excess_ci_low=bootstrap(g,'excess')[0],excess_ci_high=bootstrap(g,'excess')[1]);summary.append(row)
  if bef==5 and aft==5:
   for s,gg in g.groupby('ticker'): stocks.append(dict(high_yield=high,ticker=s,**esummary(gg)))
pd.DataFrame(summary).to_csv(P/'event_summary.csv',index=False);pd.DataFrame(stocks).to_csv(P/'stock_summary.csv',index=False)

# Independent sleeves. Ex-date receivable is valued immediately, spendable only after lag.
# No sale proceeds/receivables are double counted. Terminal values include receivables.
def simulate(before=5,after=5,high=False,cost=COST,lag=30,buyhold=False,no_o=False,keep=False):
 names=[s for s in T if not(no_o and s=='O')]
 total=np.zeros(len(DATES)); invested=np.zeros(len(DATES)); divtot=0; trades=[]; perstock=[]
 for s in names:
  d=D[s];cash=100000/len(names);shares=0.;pending=[];values=[];exposure=[];divs=0.;exit_at=-1;last_entry=None
  g=E[(E.ticker==s)&(E.before==before)&(E.after==after)]
  if high:g=g[g.ttm_yield>=.03]
  entries={int(r.a):r for r in g.itertuples()}
  # Prevent a new position whose exit lies outside sample; no future data used for outcomes.
  for i in range(START,END+1):
   date=IDX[i];price=d.Close.iloc[i]
   matured=[x for x in pending if x[0]<=date];cash+=sum(x[1] for x in matured);pending=[x for x in pending if x[0]>date]
   if shares>0 and d.Dividends.iloc[i]>0:
    amt=shares*d.Dividends.iloc[i];pending.append((date+pd.Timedelta(days=lag),amt));divs+=amt
   if shares>0 and not buyhold and i==exit_at:
    proceeds=shares*price*(1-cost);cash+=proceeds
    trades.append(dict(ticker=s,entry=last_entry['date'],exit=str(date.date()),shares=shares,buy_cash=last_entry['cash'],sale_cash=proceeds,dividend=shares*d.Dividends.iloc[last_entry['i']+1:i+1].sum()))
    shares=0.;exit_at=-1
   if buyhold:
    if cash>1e-10:
     shares+=cash/(price*(1+cost));cash=0.
   elif i in entries and shares==0 and cash>0:
    r=entries[i];last_entry=dict(date=str(date.date()),cash=cash,i=i);shares=cash/(price*(1+cost));cash=0.;exit_at=int(r.b)
   eq=cash+shares*price+sum(x[1] for x in pending)
   assert eq>0 and cash>=-1e-8 and shares>=0
   values.append(eq);exposure.append(shares*price)
  assert buyhold or shares==0
  arr=np.array(values);total+=arr;invested+=np.array(exposure);divtot+=divs
  perstock.append(dict(ticker=s,ending_value=arr[-1],CAGR=(arr[-1]/(100000/len(names)))**(1/YEARS)-1,dividends=divs))
 # Include opening $100k in drawdown and return series.
 ret=pd.Series(total,index=DATES).pct_change();ret.iloc[0]=total[0]/100000-1
 dd=total/np.maximum.accumulate(np.r_[100000,total])[1:]-1
 row=dict(before=before,after=after,high_yield=high,cost_bps=cost*10000,payment_lag=lag,buyhold=buyhold,no_o=no_o,ending_value=total[-1],CAGR=(total[-1]/100000)**(1/YEARS)-1,max_drawdown=dd.min(),annual_vol=ret.std()*np.sqrt(252),sharpe_zero_cash=ret.mean()/ret.std()*np.sqrt(252),avg_invested=(invested/total).mean(),dividends_accrued=divtot,closed_trades=len(trades))
 if keep:
  tag=('buyhold' if buyhold else f'b{before}_a{after}_'+('high' if high else 'all'))+('_noO' if no_o else '')
  pd.DataFrame(dict(equity=total,invested=invested,return_daily=ret,drawdown=dd),index=DATES).to_csv(P/f'daily_{tag}.csv',index_label='Date')
  pd.DataFrame(trades).to_csv(P/f'trades_{tag}.csv',index=False)
  pd.DataFrame(perstock).to_csv(P/f'sleeves_{tag}.csv',index=False)
 return row,total,ret
port=[]
for high in (False,True):
 for bef in (1,5):
  for aft in (0,5,10):port.append(simulate(bef,aft,high,keep=True)[0])
port.append(simulate(buyhold=True,keep=True)[0])
pd.DataFrame(port).to_csv(P/'portfolio_summary.csv',index=False)
sens=[]
for high in (False,True):
 for cost,lag,no_o in [(0,30,False),(.001,30,False),(.0005,45,False),(.0005,30,True)]:sens.append(simulate(high=high,cost=cost,lag=lag,no_o=no_o,keep=no_o)[0])
sens.append(simulate(buyhold=True,no_o=True,keep=True)[0])
pd.DataFrame(sens).to_csv(P/'sensitivity.csv',index=False)
annual=[];period=[]
for f in P.glob('daily_*.csv'):
 d=pd.read_csv(f,index_col=0,parse_dates=True)
 for y,g in d.groupby(d.index.year):annual.append(dict(strategy=f.stem[6:],year=y,return_total=(1+g.return_daily).prod()-1))
 for start,end in [('2015','2019'),('2020','2022'),('2023','2026')]:
  g=d.loc[start:end];gross=(1+g.return_daily).prod();yr=len(g)/252
  curve=(1+g.return_daily).cumprod(); dd=curve/np.maximum.accumulate(np.r_[1,curve])[1:]-1
  period.append(dict(strategy=f.stem[6:],period=start+'-'+end,CAGR=gross**(1/yr)-1,total_return=gross-1,max_drawdown=dd.min()))
pd.DataFrame(annual).to_csv(P/'annual_returns.csv',index=False);pd.DataFrame(period).to_csv(P/'periods.csv',index=False)
# Explicit primary-window attribution and robustness without synthetic O corporate action.
extra={}
for high in (False,True):
 g=E[(E.before==5)&(E.after==5)]
 if high:g=g[g.ttm_yield>=.03]
 z=g[g.ticker!='O']
 extra['high' if high else 'all']=dict(no_O=esummary(z),worst_events=g.nsmallest(5,'net_return').to_dict('records'),yearly=g.groupby('year').net_return.agg(['count','mean']).reset_index().to_dict('records'),spinoff_events=int(g.spinoff_crossing.sum()))
# Proof of event profit arithmetic: unit notional cash flow with costs.
err=max(abs((E.exit_price*(1-COST)+E.dividend_per_share)/(E.entry_price*(1+COST))-1-E.net_return))
assert err<1e-12
# Check independent reconstructed portfolio from the primary ledger and payments.
for high in (False,True):
 tag='b5_a5_'+('high' if high else 'all')
 trades=pd.read_csv(P/f'trades_{tag}.csv');daily=pd.read_csv(P/f'daily_{tag}.csv')
 pnl=(trades.sale_cash+trades.dividend-trades.buy_cash).sum()
 assert abs(100000+pnl-daily.equity.iloc[-1])<1e-6,(tag,pnl,daily.equity.iloc[-1])
 extra[tag+'_cashflow_check_error']=float(100000+pnl-daily.equity.iloc[-1])
extra.update(start=str(DATES[0].date()),end=str(DATES[-1].date()),sessions=len(DATES),years=YEARS,event_formula_max_error=err,raw_price_source='Yahoo split-adjusted Close; dividends explicitly added exactly once',O_corporate_action='Provider synthetic 1.032 split retained; no-O robustness removes this approximation')
(P/'diagnostics.json').write_text(json.dumps(extra,indent=2,default=str))
print(pd.DataFrame(summary).round(5).to_string(index=False));print('\nPORTFOLIOS');print(pd.DataFrame(port).round(5).to_string(index=False));print('\nSENSITIVITY');print(pd.DataFrame(sens).round(5).to_string(index=False))
