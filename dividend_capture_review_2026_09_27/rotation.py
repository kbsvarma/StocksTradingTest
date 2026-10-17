from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent
T='O VZ PM MO XOM CVX PEP KO JNJ PG SO DUK'.split()
D={s:pd.read_csv(P/'data'/f'{s}.csv',index_col=0,parse_dates=True) for s in T}
IX=D[T[0]].index;start=IX.searchsorted('2015-01-01');dates=IX[start:];years=(dates[-1]-dates[0]).days/365.25
C={s:d.Close.to_numpy() for s,d in D.items()};DIV={s:d.Dividends.to_numpy() for s,d in D.items()}
E=pd.read_csv(P/'events.csv');out=[]
def run(bef=5,aft=5,cost=.0005,lag=30,no_o=False,keep=False):
 events=E[(E.before==bef)&(E.after==aft)&(E.ttm_yield>=.03)]
 if no_o:events=events[events.ticker!='O']
 grouped={int(a):g.sort_values(['ttm_yield','ticker'],ascending=[False,True]).to_dict('records') for a,g in events.groupby('a')}
 cash=100000.;pending=[];positions={};hist=[];ledger=[];divtotal=0.;costtotal=0.;skip=0
 for i in range(start,len(IX)):
  date=IX[i]
  cash+=sum(v for due,v,typ in pending if due<=date);pending=[x for x in pending if x[0]>date]
  for s,pos in list(positions.items()):
   if DIV[s][i]>0:
    amt=pos['qty']*DIV[s][i];divtotal+=amt;pos['dividend']+=amt;pending.append((date+pd.Timedelta(days=lag),amt,'dividend'))
   if i==pos['exit_i']:
    sale=pos['qty']*C[s][i]*(1-cost);costtotal+=pos['qty']*C[s][i]*cost
    settle=i+(1 if date>=pd.Timestamp('2024-05-28') else 2)
    due=IX[settle] if settle<len(IX) else date+pd.Timedelta(days=5)
    pending.append((due,sale,'sale'))
    ledger.append(dict(ticker=s,entry=pos['entry'],exit=str(date.date()),buy_cash=pos['spent'],sale_cash=sale,dividend=pos['dividend'],net_pnl=sale+pos['dividend']-pos['spent'],net_return=(sale+pos['dividend'])/pos['spent']-1))
    del positions[s]
  eq=cash+sum(v for _,v,_ in pending)+sum(pos['qty']*C[s][i] for s,pos in positions.items())
  for e in grouped.get(i,[]):
   s=e['ticker']
   if s in positions:continue
   if len(positions)>=4 or cash<=.01:skip+=1;continue
   spend=min(cash,eq/4)
   q=spend/(C[s][i]*(1+cost));costtotal+=q*C[s][i]*cost
   positions[s]=dict(qty=q,exit_i=int(e['b']),entry=str(date.date()),spent=spend,dividend=0.);cash-=spend
  stock=sum(pos['qty']*C[s][i] for s,pos in positions.items())
  eq=cash+sum(v for _,v,_ in pending)+stock
  assert cash>=-1e-6 and stock<=eq+1e-6 and len(positions)<=4
  hist.append(dict(Date=date,equity=eq,invested=stock,cash=cash,receivable=sum(v for _,v,_ in pending),positions=len(positions)))
 assert not positions
 h=pd.DataFrame(hist).set_index('Date');r=h.equity.pct_change();r.iloc[0]=h.equity.iloc[0]/100000-1;h['return_daily']=r
 h['drawdown']=h.equity/np.maximum.accumulate(np.r_[100000,h.equity])[1:]-1
 l=pd.DataFrame(ledger);err=100000+l.net_pnl.sum()-h.equity.iloc[-1];assert abs(err)<1e-6
 row=dict(before=bef,after=aft,cost_bps=cost*10000,payment_lag=lag,no_o=no_o,ending_value=h.equity.iloc[-1],CAGR=(h.equity.iloc[-1]/100000)**(1/years)-1,max_drawdown=h.drawdown.min(),annual_vol=r.std()*np.sqrt(252),sharpe=r.mean()/r.std()*np.sqrt(252),avg_invested=(h.invested/h.equity).mean(),trades=len(l),win_rate=(l.net_return>0).mean(),mean_trade=l.net_return.mean(),dividends_accrued=divtotal,costs=costtotal,skip_capacity=skip,cashflow_error=err)
 if keep:
  tag=f'rotation_b{bef}_a{aft}'+('_noO' if no_o else '')
  h.to_csv(P/f'daily_{tag}.csv');l.to_csv(P/f'trades_{tag}.csv',index=False)
 return row
for bef in (1,5):
 for aft in (0,5,10):out.append(run(bef,aft,keep=True))
pd.DataFrame(out).to_csv(P/'rotation_summary.csv',index=False)
sens=[run(cost=0),run(cost=.001),run(lag=45),run(no_o=True,keep=True)]
pd.DataFrame(sens).to_csv(P/'rotation_sensitivity.csv',index=False)
print(pd.DataFrame(out).round(5).to_string(index=False));print('SENSITIVITY');print(pd.DataFrame(sens).round(5).to_string(index=False))
