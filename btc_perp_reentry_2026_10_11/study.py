from pathlib import Path
import json,gzip,itertools,hashlib
import numpy as np,pandas as pd
from numba import njit
P=Path(__file__).resolve().parent;D=P/'data'
raw=json.load(gzip.open(D/'KXBTCPERP_1m.json.gz','rt'));df=pd.DataFrame.from_dict(raw['rows'],orient='index',columns=raw['cols']);df.index=df.index.astype(int);df=df.sort_index();ts=np.arange(df.index.min(),df.index.max()+1,60);df=df.reindex(ts).astype(float)
A=df[['bid','ask','bid_lo','ask_hi','bid_hi','ask_lo']].to_numpy();valid=np.isfinite(A[:,0]+A[:,1])&(A[:,0]>0)&(A[:,1]>=A[:,0])&(A[:,1]<1e6)
A[~valid,:]=np.nan
badext=0
for col in range(2,6):
 bad=~np.isfinite(A[:,col])|(A[:,col]<=0)|(A[:,col]>=1e6);badext+=int((bad&valid).sum());A[bad,col]=np.nan
mid=pd.Series((A[:,0]+A[:,1])/2,index=ts);sig=np.zeros((len(ts),6));sig[:,0]=1;sig[:,1]=-1
cont=mid.notna().rolling(61,min_periods=61).sum().eq(61).to_numpy()
for k,lag in [(2,15),(3,60)]:sig[:,k]=np.sign(mid/mid.shift(lag)-1).fillna(0)
h=mid.rolling(60,min_periods=60).max().shift(1);l=mid.rolling(60,min_periods=60).min().shift(1);avg=mid.rolling(60,min_periods=60).mean()
sig[:,4]=np.where(mid>h,1,np.where(mid<l,-1,0));sig[:,5]=np.where(mid/avg-1<-.003,1,np.where(mid/avg-1>.003,-1,0));sig[~cont,:]=0
fund=np.zeros(len(ts));fundrecords=json.load(open(D/'KXBTCPERP_funding.json'))
for f in fundrecords:
 stamp=int(pd.Timestamp(f['funding_time']).timestamp());ix=np.searchsorted(ts,stamp)
 if ix<len(ts) and ts[ix]==stamp:fund[ix]=float(f['funding_rate'])*float(f['mark_price'])
fc=np.cumsum(fund)
@njit
def fee(p,rate,cent):
 v=p*rate
 return np.ceil(v*100)/100 if cent else v
@njit
def run(A,sig,fc,start,end,mode,tp,sl,reentry,friction,delay,rate,cent,maker):
 out=np.zeros((end-start+1,13));count=0;daily=np.zeros((end-start)//1440+3);cash=100.;position=0;entry=0.;entry_i=0;ec=0.;side=0;pending=0;due=0;reason=0;cool=start;anchor=0.;peak=100.;dd=0.;missing=0;ambiguous=0
 for i in range(start,end):
  v=np.isfinite(A[i,0]+A[i,1]);closed=False
  if position and not v:missing+=1
  closepx=0.;do_close=False
  if position and v:
   if i==end-1:do_close=True;reason=4;closepx=A[i,0] if side>0 else A[i,1]
   elif pending==2 and i>=due:do_close=True;closepx=A[i,0] if side>0 else A[i,1]
   elif i>entry_i and pending==0:
    mark=A[i,0] if side>0 else A[i,1]
    adverse=A[i,2] if side>0 else A[i,3];favorable=A[i,4] if side>0 else A[i,5]
    stop=side*(mark/entry-1)<=-sl or (np.isfinite(adverse) and side*(adverse/entry-1)<=-sl)
    target=side*(mark/entry-1)>=tp or (np.isfinite(favorable) and side*(favorable/entry-1)>=tp)
    if stop and target:ambiguous+=1
    if stop:pending=2;due=i+delay;reason=1
    elif target:
     if maker and np.isfinite(favorable) and side*(favorable/entry-1)>tp:
      do_close=True;closepx=entry*(1+side*tp);reason=2
     else:pending=2;due=i+delay;reason=2
    elif i-entry_i>=1440:pending=2;due=i+delay;reason=3
  if do_close:
   gross=side*(closepx-entry);funding=-side*(fc[i]-fc[entry_i]);exrate=.0005 if maker and reason==2 and pending!=2 else rate
   fees=fee(entry,rate,cent)+fee(closepx,exrate,cent);slips=(entry+closepx)*friction
   net=gross+funding-fees-slips;cash+=net
   out[count,:]=np.array([entry_i,i,side,entry,closepx,gross,funding,fees,slips,net,reason,i-entry_i,ambiguous]);count+=1
   position=0;pending=0;cool=i+(15 if reentry==1 else 60 if reentry==2 else 0);anchor=(A[i,0]+A[i,1])/2;closed=True
  if pending==1 and i>=due:
   if v and (A[i,1]-A[i,0])/((A[i,0]+A[i,1])/2)<=.005:
    price=A[i,1] if side>0 else A[i,0]
    if cash>=price+fee(price,rate,cent)+price*friction:
     entry=price;entry_i=i;position=1;ec=fee(price,rate,cent)+price*friction
   pending=0
  if not position and pending==0 and i>=cool and i<end-delay-1 and v:
   s=sig[i,mode];m=(A[i,0]+A[i,1])/2
   pullok=reentry!=3 or anchor==0 or (s>0 and m<=anchor*.9975) or (s<0 and m>=anchor*1.0025)
   if s!=0 and pullok:side=int(s);pending=1;due=i+delay
  eq=cash
  if position and v:
   mark=A[i,0] if side>0 else A[i,1];eq=cash+side*(mark-entry)-side*(fc[i]-fc[entry_i])-ec-fee(mark,rate,cent)-mark*friction
  peak=max(peak,eq);dd=max(dd,peak-eq);daily[(i-start)//1440]=eq
 # Last quote inside period used for final flatten; caller ensures endpoint valid.
 return out[:count],daily,dd,missing,ambiguous
NAMES=['long','short','momentum15','momentum60','breakout60','reversion60'];RE=['immediate','wait15','wait60','pullback25bp']
PERIODS={'development':('2026-06-04','2026-08-01'),'validation':('2026-08-01','2026-09-01'),'evaluation':('2026-09-01','2026-10-10T21:19:00Z')}
def bounds(period):
 a,b=PERIODS[period];start=np.searchsorted(ts,int(pd.Timestamp(a,tz='UTC').timestamp()) if 'Z' not in a else int(pd.Timestamp(a).timestamp()));end=np.searchsorted(ts,int(pd.Timestamp(b,tz='UTC').timestamp()) if 'Z' not in b else int(pd.Timestamp(b).timestamp()))
 while not valid[end-1]:end-=1
 return start,end
CONFIGS=[dict(id=j,entry=NAMES[m],tp_bps=tp,sl_bps=sl,reentry=RE[r],mode=m,re=r) for j,(m,tp,sl,r) in enumerate(itertools.product(range(6),[10,25,50,100,200],[10,25,50,100],range(4)))]
def execute(c,p,scenario='base'):
 stress={'base':(1,1,.0012,False,False),'slip5':(5,1,.0012,False,False),'delay2':(1,2,.0012,False,False),'centfees':(1,1,.0012,True,False),'zero_fees':(0,1,0.,False,False),'maker_target':(1,1,.0012,False,True)}[scenario]
 st,en=bounds(p);fr,de,rate,cent,maker=stress;o,path,dd,miss,amb=run(A,sig,fc,st,en,c['mode'],c['tp_bps']/1e4,c['sl_bps']/1e4,c['re'],fr/1e4,de,rate,cent,maker)
 cols=['entry_i','exit_i','side','entry_price','exit_price','gross','funding','fees','slippage','net','reason','minutes','cumulative_ambiguous'];ledger=pd.DataFrame(o,columns=cols)
 for col in ['entry_i','exit_i']:ledger[col.replace('_i','_ts')]=[int(ts[int(x)]) for x in ledger[col]]
 daily=path[:int(np.ceil((en-st)/1440))];pnl=np.diff(np.r_[100,daily]);r=dict(c,period=p,scenario=scenario,trades=len(o),net=float(o[:,9].sum()),gross=float(o[:,5].sum()),funding=float(o[:,6].sum()),fees=float(o[:,7].sum()),slippage=float(o[:,8].sum()),win_pct=float((o[:,9]>0).mean()*100) if len(o) else 0,mean_net=float(o[:,9].mean()) if len(o) else 0,dd=dd,worst_trade=float(o[:,9].min()) if len(o) else 0,mean_minutes=float(o[:,11].mean()) if len(o) else 0,missing_held_minutes=miss,ambiguous_bars=amb,days=(en-st)/1440,positive_days=int((pnl>0).sum()),without_best3=float(o[:,9].sum()-np.sort(o[:,9])[-3:].sum()))
 assert abs(daily[-1]-100-r['net'])<1e-7
 return r,ledger,pnl
if __name__=='__main__':
 meta={'source_rows':len(raw['rows']),'grid_minutes':len(df),'valid_close_rows':int(valid.sum()),'invalid_extreme_fields':badext,'start':str(pd.to_datetime(ts[0],unit='s',utc=True)),'end':str(pd.to_datetime(ts[-1],unit='s',utc=True)),'source_hashes':{f:hashlib.sha256((D/f).read_bytes()).hexdigest() for f in ['KXBTCPERP_1m.json.gz','KXBTCPERP_funding.json']}}
 (P/'data_audit.json').write_text(json.dumps(meta,indent=2));(P/'grid.json').write_text(json.dumps(CONFIGS,indent=2));rows=[]
 # Freeze leaders strictly before computing later periods in this script.
 for c in CONFIGS:rows.append(execute(c,'development')[0])
 dev=pd.DataFrame(rows);leaders=dev[dev.trades>=50].sort_values('net',ascending=False).groupby('entry',sort=False).head(1);overall=dev[dev.trades>=50].nlargest(5,'net');frozen=sorted(set(leaders.id)|set(overall.id))
 (P/'frozen_leaders.json').write_text(json.dumps({'family_ids':leaders.id.tolist(),'overall_ids':overall.id.tolist()},indent=2));print('Development complete',len(rows),flush=True)
 for c in CONFIGS:rows.append(execute(c,'validation')[0])
 val=pd.DataFrame(rows).query('period=="validation"');survivors=val[val.id.isin(leaders.id)&(val.net>0)&(val.trades>=20)]
 primary=None if survivors.empty else int(leaders[leaders.id.isin(survivors.id)].sort_values('net',ascending=False).iloc[0].id)
 (P/'selection.json').write_text(json.dumps({'primary_id':primary,'validation_survivors':survivors.id.tolist()},indent=2));print('Validation complete; primary',primary,flush=True)
 for c in CONFIGS:rows.append(execute(c,'evaluation')[0])
 pd.DataFrame(rows).to_csv(P/'grid_results.csv',index=False)
 stressrows=[];rng=np.random.default_rng(20261011);ci=[]
 for id in frozen:
  c=CONFIGS[id]
  for period in PERIODS:
   for scenario in ['base','slip5','delay2','centfees','zero_fees','maker_target']:
    r,ledger,pnl=execute(c,period,scenario);stressrows.append(r)
    ledger.to_parquet(P/f'ledger_{id}_{period}_{scenario}.parquet',index=False)
    if period=='evaluation' and scenario=='base':
     n=len(pnl);ix=((rng.integers(n,size=(5000,int(np.ceil(n/7))))[:,:,None]+np.arange(7))%n).reshape(5000,-1)[:,:n];means=pnl[ix].mean(axis=1)
     ci.append(dict(id=id,mean_daily=pnl.mean(),low=np.percentile(means,2.5),high=np.percentile(means,97.5)))
 pd.DataFrame(stressrows).to_csv(P/'leader_stress.csv',index=False);pd.DataFrame(ci).to_csv(P/'uncertainty.csv',index=False)
 print(pd.DataFrame(rows).query('period=="evaluation" and id in @frozen')[['id','entry','tp_bps','sl_bps','reentry','trades','net','fees','funding','dd']].to_string(index=False));print('DONE',flush=True)
