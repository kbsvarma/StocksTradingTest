from pathlib import Path
import pandas as pd,numpy as np
import exchange_calendars as exchange_cal
from options import option_crp
from portfolio_trace import traced_crp
P=Path(__file__).resolve().parent;prices=pd.read_parquet(P.parent/'cover_validation_2026_09/vendor_raw_closes.parquet')[['CI','YUMC']];days={};audit=[]
for f in sorted((P/'company_quotes').glob('*.parquet')):
 day=pd.Timestamp(f.stem);q=pd.read_parquet(f);et=q.index.tz_convert('America/New_York');minute=et.hour*60+et.minute;q=q[(minute>=945)&(minute<=946)].copy();q=q.drop_duplicates('symbol',keep='first');m=q.symbol.str.extract(r'^(CI|YUMC)\s+(\d{6})([CP])(\d{8})$');q['root']=m[0];q['expiry']=pd.to_datetime(m[1],format='%y%m%d');q['right']=m[2];q['strike']=pd.to_numeric(m[3])/1000
 q=q[q.bid_px_00.ge(0)&q.ask_px_00.ge(q.bid_px_00)&q.ask_px_00.lt(1e4)&(q.bid_sz_00.ge(1)|q.bid_px_00.eq(0))&q.ask_sz_00.ge(1)];days[day]=q.set_index('symbol')
rows=[]
for month in sorted(set(x.to_period('M') for x in days)):
 dates=list(exchange_cal.get_calendar('XNYS').sessions_in_range(month.start_time,month.end_time.normalize()).tz_localize(None))
 if len(dates)<10:continue
 entry=dates[0]
 if entry not in days:audit.append(dict(month=str(month),status='missing_fixed_entry'));continue
 prior=prices[prices.index<entry].iloc[-1];legs=[];strikes=[];expirylist=[]
 for root in ['CI','YUMC']:
  q=days[entry];dte=(q.expiry-entry).dt.days;q=q[(q.root==root)&dte.between(30,75)];chosen=None
  for exp in sorted(q.expiry.unique()):
   e=q[q.expiry==exp];ca=e[e.right=='C'].set_index('strike');pu=e[e.right=='P'].set_index('strike');ks=ca.index.intersection(pu.index)
   if len(ks):
    k=min(ks,key=lambda z:abs(z-prior[root]));chosen=(ca.loc[k].name,pu.loc[k].name,k,exp)
    # Index before set_index was symbol; recover symbol by strike/right.
    chosen=(e[(e.right=='C')&(e.strike==k)].index[0],e[(e.right=='P')&(e.strike==k)].index[0],k,exp);break
  if chosen is None:break
  legs.extend(chosen[:2]);strikes.append(chosen[2]);expirylist.append(str(chosen[3]))
 if len(legs)!=4:audit.append(dict(month=str(month),status='missing_entry_chain'));continue
 bid=[];ask=[];bc=[];ac=[];used=[]
 for day in dates:
  q=days.get(day,pd.DataFrame(columns=days[entry].columns)).reindex(legs)
  if q.bid_px_00.isna().any():audit.append(dict(month=str(month),day=str(day.date()),status='missing_held_quote'))
  used.append(day);bid.append(q.bid_px_00.to_numpy());ask.append(q.ask_px_00.to_numpy());bc.append(q.bid_sz_00.to_numpy());ac.append(q.ask_sz_00.to_numpy())
 if len(used)<10:continue
 bid=np.array(bid);ask=np.array(ask);bc=np.array(bc);ac=np.array(ac)
 missing_days=int(np.isnan(bid).any(axis=1).sum());missing_exit=bool(np.isnan(bid[-1]).any());bid[-1]=np.nan_to_num(bid[-1],nan=0.);ask[-1]=np.nan_to_num(ask[-1],nan=0.);bc=np.nan_to_num(bc,nan=0.);ac=np.nan_to_num(ac,nan=0.)
 exitq=pd.read_parquet(P/'company_exit_quotes'/f'{used[-1].date()}.parquet');et=exitq.index.tz_convert('America/New_York');exitq['minute']=et.hour*60+et.minute;exitq=exitq[exitq.symbol.isin(legs)].drop_duplicates(['minute','symbol'],keep='last')
 def emat(col):return np.column_stack([exitq[exitq.symbol==leg].set_index('minute')[col].sort_index().reindex(np.arange(945,960)).to_numpy(dtype=float) for leg in legs])
 eb=emat('bid_px_00');ea=emat('ask_px_00');ecb=np.nan_to_num(emat('bid_sz_00'));eca=np.nan_to_num(emat('ask_sz_00'));ev=(eb>=0)&(ea>=eb)&(ea>0)&(ea<1e4);eb[~ev]=np.nan;ea[~ev]=np.nan;ecb[~ev]=0;eca[~ev]=0
 for instrument,bb,aa,cap,bcap,acap in [('long_calls',bid[:,[0,2]],ask[:,[0,2]],10000.,bc[:,[0,2]],ac[:,[0,2]]),('funded_calls',bid[:,[0,2]]+np.array(strikes),ask[:,[0,2]]+np.array(strikes),100000.,bc[:,[0,2]],ac[:,[0,2]]),('funded_synthetics',np.column_stack([bid[:,0]-ask[:,1]+strikes[0],bid[:,2]-ask[:,3]+strikes[1]]),np.column_stack([ask[:,0]-bid[:,1]+strikes[0],ask[:,2]-bid[:,3]+strikes[1]]),100000.,np.minimum(bc[:,[0,2]],ac[:,[1,3]]),np.minimum(ac[:,[0,2]],bc[:,[1,3]]))]:
  xb=eb[:,[0,2]] if instrument=='long_calls' else eb[:,[0,2]]+np.array(strikes) if instrument=='funded_calls' else eb[:,[0,2]]-ea[:,[1,3]]+np.array(strikes)
  xc=ecb[:,[0,2]] if instrument in ['long_calls','funded_calls'] else np.minimum(ecb[:,[0,2]],eca[:,[1,3]])
  xm=xc.copy();xm[-1]=1e12
  for case,fee in [('base',.65),('stress',1.30)]:
   for integer in [True,False]:
    for name,step in [('hold',0),('fill_initial',-1),('daily',1),('weekly',5)]:
     cost=fee*(2 if instrument=='funded_synthetics' else 1);terminal_mark_caps=bcap.astype(float).copy();terminal_mark_caps[-1]=1e12
     marked_net=option_crp(bb,aa,step,cost,integer,cap,terminal_mark_caps,acap,xb,xm)[0]
     net,turn,ntr,left=option_crp(bb,aa,step,cost,integer,cap,bcap,acap,xb,xc)
     replay=traced_crp(bb,aa,step,cost,integer,cap,bcap,acap,xb,xc);assert abs(net-replay[0])<1e-6;path=replay[-1];dd=float(np.min(path/np.maximum.accumulate(path)-1));low=float(path.min())
     rows.append(dict(month=str(month),instrument=instrument,case=case,integer=integer,design=name,capital=cap,net=net,marked_net=marked_net,drawdown_pct=dd,min_equity=low,contracts_traded=turn,unliquidated_contracts=left,observations=len(used),missing_days=missing_days,missing_exit=missing_exit,first=str(used[1].date()),last=str(used[-1].date()),legs='|'.join(legs),expiries='|'.join(expirylist)))
 audit.append(dict(month=str(month),status='evaluated',observations=len(used)))
pd.DataFrame(rows).to_csv(P/'company_portfolio_scores.csv',index=False);pd.DataFrame(audit).to_csv(P/'company_portfolio_coverage.csv',index=False);print('company replays',len(rows),flush=True)
