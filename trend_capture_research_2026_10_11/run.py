from pathlib import Path
import importlib.util,json,gzip,sys
import numpy as np,pandas as pd
from numba import njit
P=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('prior_expansion',P/'dependencies'/'previous'/'study.py');prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
ENTRIES=['break6','compression24','ema8_32','momentum12'];EXITS=['target3R','trail1R','delayed_trail1R','channel24']
def features(close,crypto=True):
 lp=np.log(close);v=lp.diff().rolling(72,min_periods=65).std(ddof=0).shift(1)*np.sqrt(24);stop=np.maximum(.005 if crypto else .002,2*v)
 def br(h):return pd.Series(np.where(lp>lp.rolling(h).max().shift(1),1,np.where(lp<lp.rolling(h).min().shift(1),-1,0)),index=lp.index)
 comp=lp.diff().rolling(6).std(ddof=0).shift(1)<.8*lp.diff().rolling(72).std(ddof=0).shift(1)
 diff=lp.ewm(span=8,adjust=False,min_periods=32).mean()-lp.ewm(span=32,adjust=False,min_periods=32).mean();cross=np.sign(diff).where(np.sign(diff)!=np.sign(diff.shift(1)),0)
 ret=lp-lp.shift(12);sides=[br(6),br(24).where(comp,0),cross,np.sign(ret).where(abs(ret)>v,0)]
 out=[]
 for side in sides:
  side=side.where((stop<=.12)&stop.notna(),0)
  if not crypto:
   dates=pd.to_datetime(close.index,unit='s',utc=True).tz_convert('America/New_York');minutes=dates.hour*60+dates.minute;side=side.where((dates.weekday<5)&(minutes>=570)&(minutes<=900),0)
  out.append(np.c_[side.fillna(0),stop.fillna(0),close.rolling(24).min().shift(1),close.rolling(24).max().shift(1)])
 return out
@njit
def run(A,S,F,ts,st,en,exitmode,fee,fr,delay,strict,hourly,fundrate):
 cash=100.;eq=100.;peak=100.;dd=0.;pos=0;pending=0;due=0;side=0.;ep=0.;qty=0;ei=0;dist=0.;stop=0.;target=0.;water=0.;mfe=0.;cool=st;reason=0;missing=0;n=0
 out=np.zeros((en-st+1,13))
 # A: bid,ask,lowbid,highask,highbid,lowask,volume; hourly: open,open,low,high,high,low,vol,close
 for i in range(st,en):
  valid=np.isfinite(A[i,0]+A[i,1]);closed=False
  if pos and not valid:missing+=1
  px=0.
  if pending==2 and i>=due and valid:
   px=A[i,0] if side>0 else A[i,1]
   if strict and reason==1:px=min(px,stop) if side>0 else max(px,stop)
  if pending==1 and i>=due:
   if valid and A[i,6]>0:
    ep=A[i,1] if side>0 else A[i,0];spread=A[i,1]-A[i,0]
    qty=int(min(.5*cash/ep,min(2.,.02*cash)/(ep*(dist+2*(fee+fr))+spread)))
    if qty>0:pos=1;ei=i;dist*=ep;stop=ep-side*dist;target=ep+side*3*dist;water=ep;mfe=0.
   pending=0
  if pos and valid and px==0 and pending!=2:
   breach=(A[i,2]<=stop if side>0 else A[i,3]>=stop) and (hourly or i>ei)
   hit=(A[i,4]>target if side>0 else A[i,5]<target) and (hourly or i>ei)
   timed=ts[i]-ts[ei]>=30*86400 or i==en-1
   channel=S[i,0]!=99 and (((A[i,7] if hourly else A[i,0])<S[i,2]) if side>0 else ((A[i,7] if hourly else A[i,1])>S[i,3]))
   reason=1 if breach else (2 if exitmode==0 and hit else (3 if exitmode==3 and channel else (4 if timed else 0)))
   if reason:
    if hourly:
     op=A[i,0]
     px=(min(op,stop) if side>0 else max(op,stop)) if reason==1 else (target if reason==2 else A[i,7])
    elif i==en-1:px=A[i,0] if side>0 else A[i,1]
    else:pending=2;due=i+delay
  if pos and valid:
   mark=A[i,7] if hourly else (A[i,0] if side>0 else A[i,1]);liquid=px if px else mark
   funding=-qty*ep*fundrate*((ts[i]+3600)//28800-ts[ei]//28800) if hourly else -side*qty*(F[i]-F[ei])
   gross=side*qty*(liquid-ep);fees=qty*(ep+liquid)*fee;slip=qty*(ep+liquid)*fr;net=gross-fees-slip+funding
   mfe=max(mfe,side*(mark-ep)*qty)
   if px:
    out[n]=np.array([ei,i,side,qty,ep,px,gross,fees,slip,funding,net,reason,mfe]);n+=1;cash+=net;pos=0;pending=0;cool=i+(1 if hourly else 60);closed=True
   eq=cash if not pos else cash+net
   # Only completed hourly close updates watermark; NEVER current intrabar high/low.
   if pos and (hourly or ts[i]%3600==0):
    water=max(water,mark) if side>0 else min(water,mark)
    trailmult=2. if exitmode==4 else (3. if exitmode==5 else 1.)
    if exitmode==1 or (exitmode in (2,4,5) and side*(water-ep)>=trailmult*dist):
     proposed=water-side*trailmult*dist;stop=max(stop,proposed) if side>0 else min(stop,proposed)
  else:eq=cash if not pos else eq
  peak=max(peak,eq);dd=max(dd,peak-eq)
  if not closed and not pos and pending==0 and i>=cool and i<en-delay-2 and S[i,0]!=0 and S[i,0]!=99:
   side=S[i,0];dist=S[i,1];pending=1;due=i+delay
 # Missing final quote trimmed by caller. Pending exits must close terminal.
 if pos:
  i=en-1;liquid=A[i,7] if hourly else (A[i,0] if side>0 else A[i,1]);funding=-qty*ep*fundrate*((ts[i]+3600)//28800-ts[ei]//28800) if hourly else -side*qty*(F[i]-F[ei]);gross=side*qty*(liquid-ep);fees=qty*(ep+liquid)*fee;slip=qty*(ep+liquid)*fr;net=gross-fees-slip+funding
  out[n]=np.array([ei,i,side,qty,ep,liquid,gross,fees,slip,funding,net,4,mfe]);n+=1;cash+=net;dd=max(dd,peak-cash)
 return out[:n],cash,dd,missing

def execute(asset,source,ts,A,Fs,signals,fee,periods,scenarios):
 rows=[]
 for entry,S in zip(ENTRIES,signals):
  for ex,exitname in enumerate(EXITS):
   for period,st,en in periods:
    while en>st and not np.isfinite(A[en-1,0]+A[en-1,1]):en-=1
    if en<=st:continue
    for scenario,fr,delay,strict,fund in scenarios:
     mode={'target3R':0,'trail1R':1,'delayed_trail1R':2,'channel24':3,'wide2R':4,'wide3R':5}[exitname]
     o,cash,dd,missing=run(A,S,Fs,ts,st,en,mode,fee,fr,delay,strict,source=='proxy',fund)
     assert np.isfinite(cash) and abs(cash-100-o[:,10].sum())<1e-7
     rows.append(dict(asset=asset,source=source,entry=entry,exit=exitname,period=period,scenario=scenario,trades=len(o),net=cash-100,dd=dd,gross=o[:,6].sum(),fees=o[:,7].sum(),funding=o[:,9].sum(),without_best3=o[:,10].sum()-np.sort(o[:,10])[-3:].sum(),missing_held=missing,days=(ts[en-1]-ts[st])/86400,mean_hold_hours=np.mean((ts[o[:,1].astype(int)]-ts[o[:,0].astype(int)])/3600) if len(o) else 0,mfe_total=o[:,12].sum()))
     np.savez_compressed(P/'ledgers'/f'{source}_{asset}_{entry}_{exitname}_{period}_{scenario}.npz',ledger=o)
 return rows
if __name__=='__main__':
 (P/'ledgers').mkdir(exist_ok=True);rows=[];audit=[]
 if '--proxy' not in sys.argv:
  assets=[x['ticker'] for x in json.load(open(prior.P/'coverage.json'))]+['KXETHPERP','KXXRPPERP','KXBNBPERP','KXHYPEPERP','KXZECPERP']
  for asset in assets:
   ts,A,F,_=prior.data_load(asset);crypto=prior.r.old.M[asset]['asset_class']=='Crypto';mid=pd.Series(A[:,:2].mean(axis=1),index=ts);q=mid[mid.index%3600==0];fs=features(q,crypto);signals=[]
   for small in fs:
    S=np.zeros((len(ts),4));S[:,0]=99;S[:,2:]=np.nan;S[np.searchsorted(ts,q.index)]=small;signals.append(S)
   split=int(pd.Timestamp('2026-09-01' if crypto else '2026-10-01',tz='UTC').timestamp());cut=np.searchsorted(ts,split);en=min(len(ts),np.searchsorted(ts,int(pd.Timestamp('2026-10-10',tz='UTC').timestamp())))
   rows+=execute(asset,'venue',ts,A,F,signals,float(prior.r.fees['taker_fee_rates'][asset]),[('early',0,min(cut,en)),('later',min(cut,en),en)],[('base',.0001,1,0,0),('cost5',.0005,1,0,0),('delay2',.0001,2,0,0),('combined',.0005,2,1,0)])
   pd.DataFrame(rows).to_csv(P/'venue_results.csv',index=False);print(asset,'done',flush=True)
 else:
  for asset in ['BTC','ETH','SOL','ZEC']:
   raw=json.load(gzip.open(P/'data'/f'{asset}.json.gz','rt'));d=pd.DataFrame(raw['rows'],columns=['t','low','high','open','close','volume']).set_index('t').sort_index();ts=np.arange(d.index.min(),d.index.max()+3600,3600);d=d.reindex(ts);unit=float(prior.r.old.M['KX'+asset+'PERP']['contract_size']);a=d[['open','open','low','high','high','low','volume','close']].to_numpy();a[:,:6]*=unit;a[:,7]*=unit
   # Feature at previous completed hourly close, entry next open; signal decision loop one bar before fill.
   signals=features(pd.Series(d.close.to_numpy()*unit,index=ts));periods=[(str(y),np.searchsorted(ts,int(pd.Timestamp(f'{y}-01-01',tz='UTC').timestamp())),np.searchsorted(ts,int(pd.Timestamp(f'{y+1}-01-01',tz='UTC').timestamp()))) for y in range(2022,2026)]+[('all',0,len(ts))]
   rows+=execute(asset,'proxy',ts,a,np.zeros(len(ts)),signals,.0012,periods,[('base',.0001,1,0,.0001),('combined',.0005,2,1,.0003),('no_funding',.0001,1,0,0)])
   audit.append(dict(asset=asset,rows=len(ts),missing=int(d.close.isna().sum()),contract_size=unit));pd.DataFrame(rows).to_csv(P/'proxy_results.csv',index=False);print(asset,'done',flush=True)
  (P/'data_audit.json').write_text(json.dumps(audit,indent=2))
