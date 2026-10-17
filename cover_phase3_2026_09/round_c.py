from pathlib import Path
import numpy as np,pandas as pd
from numba import njit
from round_a import stats
from round_b import PAIRS
P=Path(__file__).resolve().parent;V=P.parent/'cover_validation_2026_09'
MULT=np.array([2.,5.,.5]);TICK=np.array([.25,.25,1.])
@njit(cache=True)
def replay(events,closes,reference,day,executable,stock,flags,capital,mode,integer,fee,slipticks,stockfee,w=.5,resize_active=True,stock_flags=None,margin_rate=0.,target_weights=None):
 nd=len(stock);q=np.zeros(3);last=np.zeros(3);qs=np.zeros(2);cs=capital*(1-w);ce=capital*w;scale=capital*w/reference[0];pending=ce;paths=np.zeros((nd,9));mult=np.array([2.,5.,.5]);tick=np.array([.25,.25,1.]);initialized=False;fees=0.;resizes=0.;peak_notional=0.;minmargin=1e9
 for i in range(len(day)):
  if initialized:
   for j in range(3):
    if not np.isfinite(closes[i,j]):continue
    en,ep,xn,xp,rq=events[i,j]
    if en>0:
     assert q[j]==0
     raw=en*scale;q[j]=np.rint(raw) if integer else raw
     if margin_rate>0:
      others=0.
      for k0 in range(3):
       if k0!=j:others+=q[k0]*last[k0]*mult[k0]*margin_rate
      cap=max(0.,ce-others)/(ep*mult[j]*margin_rate+fee/2);q[j]=min(q[j],np.floor(cap) if integer else cap)
     if xn>0:ce+=q[j]*(xp-ep)*mult[j]-fee*q[j];fees+=fee*q[j];q[j]=0.
     else:ce+=q[j]*(closes[i,j]-ep)*mult[j]-fee/2*q[j];fees+=fee/2*q[j]
    elif q[j]>0:
     if xn>0:ce+=q[j]*(xp-last[j])*mult[j]-fee/2*q[j];fees+=fee/2*q[j];q[j]=0.
     else:ce+=q[j]*(closes[i,j]-last[j])*mult[j]
    last[j]=closes[i,j]
  k=day[i]
  if k>=0:
   sv=cs+np.sum(qs*stock[k]);act=(k==0 or (mode!=0 and flags[k])) and executable[i]
   if not initialized:
    last=closes[i].copy();initialized=True
   if act and k>0:
    delta=pending-ce
    if mode==4:delta*=.5
    if delta>0:
     used=min(cs,delta);cs-=used;ce+=used;delta-=used
     value=np.sum(qs*stock[k]);sell=min(value,delta/(1-stockfee));frac=sell/value if value>0 else 0.
     qs*=1-frac;ce+=sell*(1-stockfee);fees+=sell*stockfee
    else:
     buffer=margin_rate*np.sum(q*last*mult);transfer=min(-delta,max(0.,ce-buffer));ce-=transfer;cs+=transfer
    scale=max(0.,ce/reference[i])
   # Daily constant rebalance within the stock sleeve; all proceeds stay funded.
   val=qs*stock[k];sv=cs+val.sum();post=sv;sw=np.ones(2)*.5
   if stock_flags is not None and not stock_flags[k] and val.sum()>0:sw=val/val.sum()
   for _ in range(20):post=sv-stockfee*np.abs(post*sw-val).sum()
   target=sw*post;diff=target-val
   for j in range(2):
    if diff[j]<0:cs+=(-diff[j])*(1-stockfee);qs[j]+=diff[j]/stock[k,j];fees+=(-diff[j])*stockfee
   need=np.maximum(diff,0).sum()*(1+stockfee);f=min(1.,max(0.,cs)/need) if need>0 else 0.
   for j in range(2):
    if diff[j]>0:amt=diff[j]*f;cs-=amt*(1+stockfee);qs[j]+=amt/stock[k,j];fees+=amt*stockfee
   if act and (resize_active or k==0):
    for j in range(3):
     raw=events[i,j,4]*scale;new=np.rint(raw) if integer else raw
     if margin_rate>0:
      others=0.
      for k0 in range(3):
       if k0!=j:others+=q[k0]*last[k0]*mult[k0]*margin_rate
      cap=max(0.,ce-others)/(last[j]*mult[j]*margin_rate+fee/2);new=min(new,np.floor(cap) if integer else cap)
     change=abs(new-q[j]);cost=change*(fee/2+slipticks*tick[j]*mult[j]);ce-=cost;fees+=cost;resizes+=change;q[j]=new
   sv=cs+np.sum(qs*stock[k]);nav=ce+sv;pending=nav*(w if target_weights is None else target_weights[k])
   notional=np.sum(q*last*mult);peak_notional=max(peak_notional,notional);minmargin=min(minmargin,ce-.2*notional)
   paths[k]=np.array([nav,ce,sv,fees,resizes,notional,ce-.1*notional,ce-.2*notional,ce-.3*notional]);assert cs>-1e-6
  if initialized:
   notional=np.sum(q*last*mult);peak_notional=max(peak_notional,notional);minmargin=min(minmargin,ce-.2*notional)
 terminalcost=np.sum(q*(fee/2+slipticks*tick*mult))+np.sum(qs*stock[-1])*stockfee;paths[-1,0]-=terminalcost;fees+=terminalcost
 return paths,fees,resizes,peak_notional,minmargin

def inputs(parent,case,prices,start,end):
 import exchange_calendars as xc
 dates=prices.loc[start:end].index;cal=xc.get_calendar('XNYS');sched=cal.schedule.loc[str(dates[0].date()):str(dates[-1].date())];close=pd.Series(pd.DatetimeIndex(sched['close']),index=sched.index.tz_localize(None) if sched.index.tz is not None else sched.index).reindex(dates);times=pd.DatetimeIndex(close)
 data=pd.read_parquet(P/f'core_events_{parent}_{case}.parquet');data.index+=pd.Timedelta(minutes=15)
 original=data.index;ix=original.union(times);ix=ix[(ix>=times[0])&(ix<=times[-1])];data=data.reindex(ix);executable=ix.isin(original);assert len(ix)>0
 for sym in ['NQ','ES','YM']:data[(sym,'ref_qty')]=data[(sym,'ref_qty')].ffill()
 ev=np.stack([data[s][['entry_qty','entry_px','exit_qty','exit_px','ref_qty']].fillna(0).to_numpy() for s in ['NQ','ES','YM']],axis=1);cl=np.column_stack([data[s]['close'].ffill().to_numpy() for s in ['NQ','ES','YM']]);assert np.isfinite(cl).all()
 ref=sum(data[s]['net'].ffill().fillna(0) for s in ['NQ','ES','YM']).to_numpy();ref=50000+ref-ref[0];assert ref.min()>0
 di=np.full(len(ix),-1,dtype=np.int64);loc=ix.get_indexer(times);assert min(loc)>=0;di[loc]=np.arange(len(dates));return dates,ev,cl,ref,di,executable

def main():
 prices=pd.read_parquet(V/'long_eligible_prices.parquet');rows=[];paths={}
 for parent in ['baseline','ridge10','width20']:
  for case,fee,slip,sfee in [('stress',7.,1,.001),('severe',14.,2,.0025)]:
   for period,start,end in [('full','2018-05-01','2026-07-02'),('early','2018-05-01','2021-12-31'),('discovery','2022','2025-12-31'),('recent','2026','2026-07-02')]:
    ix,ev,cl,ref,di,executable=inputs(parent,case,prices,start,end);flags={'daily':np.ones(len(ix),dtype=bool),'weekly':np.r_[True,ix.to_period('W')[1:]!=ix.to_period('W')[:-1]],'monthly':np.r_[True,ix.to_period('M')[1:]!=ix.to_period('M')[:-1]]}
    for a,b in PAIRS:
     for capital in [50000.,100000.,250000.]:
      for integer in [False,True]:
       for name,mode,w in [('static_blend',0,.5),('daily',1,.5),('weekly',2,.5),('monthly',3,.5),('half_daily',4,.5),('core',0,1.),('stock_pair',0,0.)]:
        p,fees,resizes,peak,margin=replay(ev,cl,ref,di,executable,prices.loc[ix,[a,b]].to_numpy(),flags.get(name,flags['daily']),capital,mode,integer,fee,slip,sfee,w)
        rows.append(dict(parent=parent,case=case,period=period,a=a,b=b,capital=capital,integer=integer,design=name,fees=fees,resizes=resizes,peak_notional=peak,min_margin20=margin,**stats(p[:,0]/capital,ix)))
        if parent=='baseline' and capital==100000 and integer and case=='stress' and period=='full':paths[f'{a}_{b}__{name}']=pd.Series(p[:,0]/capital,index=ix)
    print(parent,case,period,'done',flush=True)
 d=pd.DataFrame(rows);d.to_csv(P/'round_c_scores.csv',index=False);pd.DataFrame(paths).to_parquet(P/'round_c_paths.parquet');keys=['parent','case','period','a','b','capital','integer'];base=d[d.design=='static_blend'][keys+['cagr','maxdd','sharpe']].rename(columns={k:'static_'+k for k in ['cagr','maxdd','sharpe']});d=d.merge(base,on=keys);d['cagr_change']=d.cagr-d.static_cagr;d['dd_change']=d.maxdd-d.static_maxdd;d['sharpe_change']=d.sharpe-d.static_sharpe;d.to_csv(P/'round_c_comparisons.csv',index=False)
 print(d[(d.capital==100000)&d.integer&(d.case=='stress')].groupby(['period','design'])[['cagr_change','dd_change','sharpe_change']].median().round(4).to_string())
if __name__=='__main__':main()
