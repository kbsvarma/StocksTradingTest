"""Causal daily fixed-weight ledger; split-adjusted share units, explicit dividend receivables."""
import numpy as np
from numba import njit
@njit(cache=True)
def run(signal,bid,ask,mark,dividend,receipt_index,extra,rebalance=True,capital=1000.,bid_capacity=None,ask_capacity=None):
 n=len(mark);shares=np.zeros(2);cash=capital;receivable=0.;due=np.zeros((n+1,2));nav=np.zeros(n);turn=0.;skips=0;maxerr=0.;trades=0;mincash=capital;maxquantity=np.zeros((n,2));started=False;partial=0
 for t in range(n):
  # Entitlement is determined by shares held before ex-date trading.
  for j in range(2):
   amount=shares[j]*dividend[t,j]
   if amount>0:
    receivable+=amount;due[min(receipt_index[t],n),j]+=amount
  receipts=due[t].copy();cash+=receipts.sum();receivable-=receipts.sum()
  good=np.isfinite(signal[t]).all() and np.isfinite(bid[t]).all() and np.isfinite(ask[t]).all() and (bid[t]>0).all() and (ask[t]>=bid[t]).all()
  if good and (rebalance or not started):
   value=(shares*signal[t]).sum()+cash
   target=value*.5/signal[t];delta=target-shares
   # Sales precede buys. Cash and cost constraints are enforced at execution.
   for j in range(2):
    if delta[j]<0:
     q=-delta[j]
     if bid_capacity is not None:
      available=max(0.,bid_capacity[t,j]) if np.isfinite(bid_capacity[t,j]) else 0.
      if q>available:partial+=1;q=available
     cash+=q*bid[t,j]*(1-extra);shares[j]-=q;turn+=q*bid[t,j];maxquantity[t,j]=-q
     if q>0:trades+=1
   for j in range(2):
    if delta[j]>0:
     q=min(delta[j],max(cash,0.)/(ask[t,j]*(1+extra)))
     if ask_capacity is not None:
      available=max(0.,ask_capacity[t,j]) if np.isfinite(ask_capacity[t,j]) else 0.
      if q>available:partial+=1;q=available
     cash-=q*ask[t,j]*(1+extra);shares[j]+=q;turn+=q*ask[t,j];maxquantity[t,j]=q
     if q>0:trades+=1
   started=True
   invested=(shares*signal[t]).sum()
   if invested>0:maxerr=max(maxerr,abs(shares[0]*signal[t,0]/invested-.5))
  elif not good:skips+=1
  elif not rebalance:
   # Same-name DRIP on modeled payment date; unspent dividends remain in cash.
   for j in range(2):
    q=min(receipts[j],cash)/(ask[t,j]*(1+extra))
    if ask_capacity is not None:
     available=max(0.,ask_capacity[t,j]) if np.isfinite(ask_capacity[t,j]) else 0.
     if q>available:partial+=1;q=available
    shares[j]+=q;cash-=q*ask[t,j]*(1+extra);turn+=q*ask[t,j];maxquantity[t,j]=q
    if q>0:trades+=1
  mincash=min(mincash,cash)
  nav[t]=(shares*mark[t]).sum()+cash+receivable
 # Liquidate at final available execution quote, leaving unpaid receivables at face value.
 exit_depth_insufficient=False
 if bid_capacity is not None:exit_depth_insufficient=bool((shares>bid_capacity[-1]+1e-8).any())
 if np.isfinite(bid[-1]).all() and (bid[-1]>0).all():nav[-1]=cash+receivable+(shares*bid[-1]*(1-extra)).sum()
 return nav,turn,skips,maxerr,trades,mincash,maxquantity,partial,exit_depth_insufficient,shares.copy()
if __name__=='__main__':
 p=np.full((20,2),100.);d=np.zeros_like(p);r=np.arange(20)
 nav,turn,skip,err,trades,cash,q,partial,exitflag,terminalshares=run(p,p,p,p,d,r,0.)
 assert np.allclose(nav,1000) and abs(turn-1000)<1e-8 and cash>=-1e-8
 # A change to a future signal cannot affect prior portfolio values.
 z=p.copy();z[10:,0]=110
 x=run(z,z,z,z,d,r,.001)[0]; y=run(p,p,p,p,d,r,.001)[0];assert np.array_equal(x[:10],y[:10])
 # Split-adjusted units: scaling both prices and dividends changes no NAV.
 d[5]=1.;a=run(p,p,p,p,d,r,0.)[0];b=run(p*3,p*3,p*3,p*3,d*3,r,0.)[0];assert np.allclose(a,b)
 # Ex-date price drop is offset by a receivable, without premature cash spending.
 p=np.full((20,2),100.);p[5:]=99.;d=np.zeros_like(p);d[5]=1.;r=np.full(20,20)
 nav=run(p,p,p,p,d,r,0.)[0];assert np.allclose(nav,1000)
 p=np.full((20,2),100.);d=np.zeros_like(p);r=np.arange(20);cap=np.ones_like(p);v=run(p,p,p,p,d,r,0.,True,1000.,cap,cap);assert np.allclose(v[0],1000) and v[7]>0 and np.max(np.abs(v[6]))<=1
 print('cash ledger capacity, constant-price, capital conservation, causal-prefix, unit invariance and dividend-receivable checks passed')
