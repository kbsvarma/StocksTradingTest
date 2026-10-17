from pathlib import Path
import pandas as pd,numpy as np
from scipy.special import ndtr
from numba import njit
P=Path(__file__).resolve().parent;ROOT=P.parent;Q=ROOT/'spx_spread_bot/backtests/narrow_width_databento_2026_05_21'
def delta(s,k,t,price):
 lo=np.full(len(s),.0001);hi=np.full(len(s),5.);valid=np.isfinite(price)&np.isfinite(s)&(price>=abs(s-k))&(price<2*s)&(t>0)
 for _ in range(45):
  sig=(lo+hi)/2;d1=(np.log(s/k)+.5*sig*sig*t)/(sig*np.sqrt(t));d2=d1-sig*np.sqrt(t);v=s*(2*ndtr(d1)-1)-k*(2*ndtr(d2)-1);take=v<price;lo=np.where(take,sig,lo);hi=np.where(take,hi,sig)
 sig=(hi+lo)/2;d1=(np.log(s/k)+.5*sig*sig*t)/(sig*np.sqrt(t));ans=2*ndtr(d1)-1;ans[~valid]=np.nan;return ans,sig
@njit(cache=True)
def hedge(spot,desired,step,band,bps):
 q=0.;cash=0.;cost=0.;turn=0.;peak=0.;trades=0
 for i in range(len(spot)-1):
  if not np.isfinite(spot[i]):continue
  target=desired[i]
  if not np.isfinite(target):continue
  act=i==0 or (step>0 and i%step==0) or (band>0 and abs(target-q)>=band)
  if act:
   target=np.rint(target);change=target-q;f=abs(change)*spot[i]*bps;cash-=change*spot[i]+f;cost+=f;turn+=abs(change);trades+=abs(change)>0;q=target
  peak=max(peak,abs(q)*spot[i])
 cash+=q*spot[-1]-abs(q)*spot[-1]*bps;cost+=abs(q)*spot[-1]*bps;turn+=abs(q)
 return cash,cost,turn,peak,trades
@njit(cache=True)
def option_crp(bid,ask,step,fee,integer,capital=10000.,bidcap=None,askcap=None,exit_bid=None,exit_cap=None):
 q=np.zeros(2);cash=capital;turn=0.;count=0;fixed_target=np.zeros(2)
 for i in range(1,len(bid)-1):
  if not np.isfinite(bid[i]).all() or not np.isfinite(ask[i]).all():continue
  if i>1 and step>=0 and (step==0 or (i-1)%step):continue
  # Previous observable mid determines quantity; next quote executes it.
  mark=(bid[max(0,i-1)]+ask[max(0,i-1)])/2
  if not np.isfinite(mark).all() or np.min(mark)<=0:continue
  nav=cash+np.sum(q*mark)*100;target=.5*nav/(mark*100)
  if integer:target=np.floor(target)
  if i==1:fixed_target=target.copy()
  if step==-1:target=fixed_target.copy()
  diff=target-q
  for j in range(2):
   if diff[j]<0:
    amt=-diff[j]
    if bidcap is not None:amt=min(amt,bidcap[i,j])
    cash+=amt*(bid[i,j]*100-fee);q[j]-=amt;turn+=amt;count+=1
  for j in range(2):
   if diff[j]>0:
    amt=min(diff[j],max(0,cash-fee*q.sum())/(ask[i,j]*100+2*fee))
    if askcap is not None:amt=min(amt,askcap[i,j])
    if integer:amt=np.floor(amt)
    cash-=amt*(ask[i,j]*100+fee);q[j]+=amt;turn+=amt;count+=amt>0
 assert cash>=-1e-5
 if exit_bid is not None:
  for ii in range(len(exit_bid)):
   for j in range(2):
    if not np.isfinite(exit_bid[ii,j]):continue
    amt=min(q[j],exit_cap[ii,j]);cash+=amt*(exit_bid[ii,j]*100-fee);q[j]-=amt;turn+=amt
  return cash-capital,turn,count,q.sum()
 sold=q if bidcap is None else np.minimum(q,bidcap[-1]);left=np.sum(q-sold);cash+=np.sum(sold*(bid[-1]*100-fee));turn+=sold.sum();return cash-capital,turn,count,left

def main():
 u=pd.read_parquet(ROOT/'trend_replication_2026_07/data/etf/SPY_1m.parquet');et=u.index.tz_convert('America/New_York');u['day']=et.strftime('%Y-%m-%d');u['minute']=et.hour*60+et.minute;under={d:g.drop_duplicates('minute').set_index('minute') for d,g in u.groupby('day')};rows=[];opts=[];audit=[];saved=[]
 import exchange_calendars as xc
 schedule=xc.get_calendar('XNYS').schedule;closes={str(d.date()):int(t.tz_convert('America/New_York').hour*60+t.tz_convert('America/New_York').minute) for d,t in schedule.close.items()}
 fresh=sorted((P/'fresh_spy').glob('*.parquet'));files=fresh or sorted((Q/'spy_c_cbbo_1m').glob('*.parquet'))
 for ii,f in enumerate(files):
  day=f.stem if f.parent.name=='fresh_spy' else str(pd.Timestamp(f.stem[-8:]).date())
  if day not in under:continue
  if f.parent.name=='fresh_spy':q=pd.read_parquet(f)
  else:
   other=Q/'spy_p_cbbo_1m'/f.name.replace('spy_c_','spy_p_')
   if not other.exists():audit.append(dict(day=day,status='missing_put_file'));continue
   q=pd.concat([pd.read_parquet(f),pd.read_parquet(other)])
  et=q.index.tz_convert('America/New_York');q['minute']=et.hour*60+et.minute
  # CBBO ts_recv is interval-end. ts_event is last trade, not quote age.
  age=(q.index-pd.to_datetime(q.ts_event,utc=True)).total_seconds() if isinstance(q.ts_event,pd.DatetimeIndex) else (pd.Series(q.index,index=q.index)-pd.to_datetime(q.ts_event,utc=True)).dt.total_seconds().to_numpy()
  parsed=q.symbol.str.extract(r'^SPY\s+(\d{6})([CP])(\d{8})$');q['expiry']=pd.to_datetime(parsed[0],format='%y%m%d');q['right']=parsed[1];q['strike']=pd.to_numeric(parsed[2])/1000
  valid=q.bid_px_00.ge(0)&q.ask_px_00.ge(q.bid_px_00)&q.ask_px_00.gt(0)&q.ask_px_00.lt(1e5)&(q.bid_sz_00.ge(1)|q.bid_px_00.eq(0))&q.ask_sz_00.ge(1);q['valid']=valid;g=under[day];end=min(885,closes.get(day,960)-15);mins=np.arange(600,end+1);sp=g.open.reindex(mins).to_numpy()
  if not np.isfinite(sp).all() or 599 not in g.index:audit.append(dict(day=day,status='missing_underlying_minutes'));continue
  spot=g.loc[599,'close'];entry=q[(q.minute==600)&q.valid].drop_duplicates('symbol',keep='last')
  for tenor in ['0dte','1to7dte']:
   dt=(entry.expiry-pd.Timestamp(day)).dt.days;e=entry[dt.eq(0) if tenor=='0dte' else dt.between(1,7)]
   if len(e)==0:audit.append(dict(day=day,tenor=tenor,status='no_chain'));continue
   expiry=e.expiry.min();e=e[e.expiry==expiry];ca=e[e.right=='C'].set_index('strike');pu=e[e.right=='P'].set_index('strike');ks=ca.index.intersection(pu.index)
   if len(ks)==0:continue
   k=min(ks,key=lambda x:abs(x-spot))
   if abs(k-spot)>.51:audit.append(dict(day=day,tenor=tenor,status='atm_outside_coverage'));continue
   legs=[ca.loc[k,'symbol'],pu.loc[k,'symbol']];sel=q[q.symbol.isin(legs)].drop_duplicates(['minute','symbol'],keep='last')
   def mat(col):return np.column_stack([sel[sel.symbol==leg].set_index('minute')[col].sort_index().reindex(mins,method='ffill',tolerance=2).to_numpy() for leg in legs])
   bid=mat('bid_px_00');ask=mat('ask_px_00');bc=mat('bid_sz_00');ac=mat('ask_sz_00');ok=(mat('valid')==True);bid[~ok]=np.nan;ask[~ok]=np.nan;bc=np.nan_to_num(bc);ac=np.nan_to_num(ac)
   # Both entry and next-minute execution must exist; missing terminal bid conservatively zero.
   if not np.isfinite(ask[:2]).all():audit.append(dict(day=day,tenor=tenor,status='entry_not_executable'));continue
   missing_exit=not np.isfinite(bid[-1]).all();bid[-1]=np.nan_to_num(bid[-1],nan=0.);premium=ask[1].sum()*100;value=bid[-1].sum()*100
   remaining=((expiry-pd.Timestamp(day)).days+(closes.get(day,960)-mins)/1440)/365;de,iv=delta(sp,k,remaining,np.mean(np.stack([bid,ask]),axis=0).sum(axis=1));desired=-100*de
   for case,commission,bps in [('base',.65,.0001),('stress',1.30,.0003)]:
    for name,step,band in [('unhedged',0,0),('static_delta',0,0),('every5',5,0),('every15',15,0),('every60',60,0),('band5',0,5),('band10',0,10),('band20',0,20)]:
     hp,hc,turn,peak,trades=(0.,0.,0.,0.,0) if name=='unhedged' else hedge(sp[1:],desired[:-1],step,band,bps)
     rows.append(dict(day=day,tenor=tenor,case=case,design=name,strike=k,premium=premium,option_pnl=value-premium-4*commission,hedge_pnl=hp,hedge_cost=hc,net=value-premium-4*commission+hp,shares_traded=turn,max_hedge_notional=peak,hedge_trades=trades,missing_exit=missing_exit,last_trade_timestamp_unknown_fraction=float(np.isnan(age).mean()),iv_entry=iv[0],valid_delta_fraction=np.isfinite(de).mean()))
    for name,step in [('hold',0),('fill_initial',-1),('rebalance15',15),('rebalance60',60)]:
     for integer in [True,False]:
      pnl,turn,count,left=option_crp(bid,ask,step,commission,integer,10000.,bc,ac);opts.append(dict(day=day,tenor=tenor,case=case,design=name,integer=integer,net=pnl,contracts_traded=turn,trades=count,unliquidated_contracts=left,missing_exit=missing_exit))
   saved.append(dict(day=day,tenor=tenor,strike=k,expiry=str(expiry.date()),minutes=mins,spot=sp,bid=bid,ask=ask,bidcap=bc,askcap=ac))
   audit.append(dict(day=day,tenor=tenor,status='evaluated',missing_exit=missing_exit))
  if ii%200==0:print(ii,'files',len(rows),'hedge replays',flush=True)
 pd.DataFrame(rows).to_parquet(P/'spy_hedge_trades.parquet');pd.DataFrame(opts).to_parquet(P/'spy_option_crp_trades.parquet');pd.DataFrame(audit).to_csv(P/'spy_coverage.csv',index=False)
 import pickle
 with (P/'spy_selected_quotes.pkl').open('wb') as f:pickle.dump(saved,f)
 print('DONE',len(rows),len(opts),flush=True)
if __name__=='__main__':main()
