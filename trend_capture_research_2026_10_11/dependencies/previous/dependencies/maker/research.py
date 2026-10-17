from pathlib import Path
import sys,json,gzip,math,hashlib
import numpy as np,pandas as pd
from numba import njit
P=Path(__file__).resolve().parent;ROOT=P.parent
sys.path.insert(0,str(P/'inputs'/'expansion'))
import engine as old
D=old.D
fees=json.load(open(D/'fee_receipt.json'))['response']
@njit
def replay(A,S,F,st,en,maker,ttl,friction,delay,closecross,miss,feeT,feeM,tick,capital=100.,relative_tp=0.,adverse_stop=0):
 # A: bid ask bidlow askhigh bidhigh asklow volume; S side stop target maxage cooldown reentry
 out=np.zeros((en-st+1,18));n=0;cash=capital;peak=capital;dd=0.;pos=0;pending=0;due=0;until=0;side=0;limit=0.;ep=0.;ei=0;qty=0;stop=0.;target=0.;age=0;cooldown=0;cool=st;anchor=0.;reason=0;entrymaker=0;signal_i=0;post_i=0;attempts=0;missed=0;amb=0;missing=0;held=0;path=np.zeros(en-st)
 for i in range(st,en):
  valid=np.isfinite(A[i,0]+A[i,1]);closed=False;newfill=False;exitpx=0.;exitmaker=0
  # Pending taker exit, terminal flatten; never invent a missing quote.
  if pos and valid and ((pending==2 and i>=due) or i==en-1):
   exitpx=A[i,0] if side>0 else A[i,1]
   if reason==1 and adverse_stop:exitpx=min(exitpx,ep*(1-stop)) if side>0 else max(exitpx,ep*(1+stop))
   if i==en-1:reason=4
  # Pending entry posting or expiry. Size is fixed at posting, no future-aware fills.
  if not pos and pending==1 and i>=due:
   if post_i<0:
    if i>due or not valid:pending=0;missed+=1
    else:
     limit=(A[i,0] if side>0 else A[i,1]) if maker else (A[i,1] if side>0 else A[i,0])
     limit=(math.floor(limit/tick+1e-8) if side>0 else math.ceil(limit/tick-1e-8))*tick if maker else limit
     qty=int(min(.5*cash/limit,min(2.,.02*cash)/(limit*(stop+2*(feeT+friction)+(A[i,1]-A[i,0])/limit))))
     if qty<1 or side*(target-limit)<=0 or (A[i,1]-A[i,0])/limit>.005 or (maker and side*(limit-(A[i,1] if side>0 else A[i,0]))>=0):pending=0;missed+=1
     else:
      post_i=i;until=i+ttl
      if maker==0:pos=1;ep=limit;ei=i;entrymaker=0;pending=0;newfill=True
   elif i>until:pending=0;missed+=1
   elif valid and i>post_i:
    cross=A[i,1] if side>0 else A[i,0]
    if not closecross:cross=A[i,5] if side>0 else A[i,4]
    hit=np.isfinite(cross) and side*(cross-limit)<-1e-10 and A[i,6]>=qty
    accept=not miss or ((i*1103515245+12345)%2147483647)%2==0
    if hit and accept:pos=1;ep=limit;ei=i;entrymaker=1;pending=0;newfill=True
  # Stops win any unknown ordering, including the entry-fill bar.
  if newfill and relative_tp>0:
   target=ep*(1+side*relative_tp)
   target=(math.ceil(target/tick-1e-8) if side>0 else math.floor(target/tick+1e-8))*tick
  if pos and valid and exitpx==0 and pending!=2:
   mark=A[i,0] if side>0 else A[i,1];adverse=A[i,2] if side>0 else A[i,3]
   stophit=side*(mark/ep-1)<=-stop or (np.isfinite(adverse) and side*(adverse/ep-1)<=-stop)
   fav=A[i,4] if side>0 else A[i,5]
   if closecross and maker==2:fav=mark
   targethit=np.isfinite(fav) and side*(fav-target)>1e-10
   if stophit:
    pending=2;due=i+delay;reason=1
    if targethit:amb+=1
   elif newfill and maker==2 and side*(mark-target)>=0:
    pending=2;due=i+delay;reason=2
   elif not newfill and targethit:
    if maker==2 and A[i,6]>=qty:
     accept=not miss or ((i*1103515245+54321)%2147483647)%2==0
     if accept:exitpx=target;exitmaker=1;reason=2
    elif maker!=2:pending=2;due=i+delay;reason=2
   if exitpx==0 and pending!=2 and i-ei>=age:pending=2;due=i+delay;reason=3
  if exitpx>0:
   gross=side*qty*(exitpx-ep);fund=-side*qty*(F[i]-F[ei]);cost=qty*(ep*(feeM if entrymaker else feeT)+exitpx*(feeM if exitmaker else feeT));slip=qty*(ep+exitpx)*friction;net=gross+fund-cost-slip;cash+=net
   out[n,:]=np.array([signal_i,post_i,ei,i,side,qty,ep,exitpx,gross,fund,cost,slip,net,reason,entrymaker,exitmaker,stop,target]);n+=1;pos=0;pending=0;cool=i+cooldown;anchor=(A[i,0]+A[i,1])/2;closed=True
  if not pos and pending==0 and not closed and i>=cool and i<en-delay-ttl-1 and valid and S[i,0]!=0:
   sd=int(S[i,0]);mid=(A[i,0]+A[i,1])/2
   pull=S[i,5]==0 or anchor==0 or sd*(mid/anchor-1)<=-.0025
   if pull:
    side=sd;stop=S[i,1];target=S[i,2];age=int(S[i,3]);cooldown=int(S[i,4]);pending=1;due=i+delay;post_i=-1;signal_i=i;attempts+=1
    target=(math.ceil(target/tick-1e-8) if side>0 else math.floor(target/tick+1e-8))*tick
  eq=cash
  if pos:
   held+=1
   if valid:
    px=A[i,0] if side>0 else A[i,1];eq=cash+side*qty*(px-ep)-side*qty*(F[i]-F[ei])-qty*(ep*((feeM if entrymaker else feeT)+friction)+px*(feeT+friction))
   else:missing+=1;eq=path[i-st-1] if i>st else cash
  peak=max(peak,eq);dd=max(dd,peak-eq);path[i-st]=eq
 return out[:n],path,dd,attempts,missed,amb,missing,held

def load(t):
 if t=='KXBTCPERP':
  sys.path.insert(0,str(P/'inputs'/'btc_previous'));import study as btc
  A=np.c_[btc.A,btc.df['vol'].fillna(0).to_numpy()];ts=btc.ts.copy();F=btc.fc.copy()
  # Candle volume is available; aggregate volume still cannot establish queue priority or executable depth.
  return ts,A,F,btc
 raw=json.load(gzip.open(D/(t+'_1m.json.gz'),'rt'))['candlesticks'];rows={}
 for c in raw:
  def val(side,key):
   try:v=float(c[side][key]);return v if 0<v<1e6 else np.nan
   except:return np.nan
  rows[int(c['end_period_ts'])]=[val('bid','close'),val('ask','close'),val('bid','low'),val('ask','high'),val('bid','high'),val('ask','low'),float(c.get('volume') or 0)]
 ts=np.arange(min(rows),max(rows)+1,60);A=pd.DataFrame.from_dict(rows,orient='index').reindex(ts).to_numpy();bad=~np.isfinite(A[:,0]+A[:,1])|(A[:,1]<A[:,0]);A[bad,:6]=np.nan
 F=np.zeros(len(ts))
 for f in json.load(open(D/(t+'_funding.json')))['funding_rates']:
  s=old.TS(f['funding_time']);i=np.searchsorted(ts,s)
  if i<len(ts):F[i]+=float(f['funding_rate'])*float(f['mark_price'])
 return ts,A,np.cumsum(F),None

def signals(t,rule,ts,A,btc):
 S=np.zeros((len(ts),6));mid=(A[:,0]+A[:,1])/2
 if btc is not None:
  c=next(c for c in btc.CONFIGS if c['id']==int(rule));sd=btc.sig[:,c['mode']];S[:,0]=sd;S[:,1]=c['sl_bps']/1e4;S[:,2]=mid*(1+sd*c['tp_bps']/1e4);S[:,3]=1440;S[:,4]=15 if c['re']==1 else 60 if c['re']==2 else 0;S[:,5]=int(c['re']==3)
  return S
 Q,F=old.load(t,60);hour=rule.startswith('h_');source=old.load(t,3600)[0] if hour else Q;I=old.indicators(t,source,3600 if hour else 60,'hour' if hour else'minute')
 for i,s in enumerate(ts):
  sig=old.signal(t,rule,int(s),source,I)
  if not sig:continue
  sd=sig['side'];stop=sig['stop'];meta=sig['meta'];age=4320 if hour else 2*meta['w']*5
  if meta['family']=='reversal':
   target=math.exp(meta['mean']);zstop=math.exp(meta['mean']-sd*4*meta['sd']);stop=min(stop,max(0,sd*(mid[i]-zstop)/mid[i]))
  else:target=mid[i]*(1+sd*2*stop)
  if not hour:
   d=pd.Timestamp(s,unit='s',tz='UTC').tz_convert('America/New_York');age=min(age,955-(d.hour*60+d.minute))
  if stop>0:S[i]=[sd,stop,target,age,60 if hour else 15,0]
 return S
SCEN={'base':(1,1,0,0,1),'closecross':(1,1,1,0,1),'miss50':(1,1,0,1,1),'slip5':(5,1,0,0,1),'delay2':(1,2,0,0,1),'highmaker':(1,1,0,0,2.5),'adverse_stop':(1,1,0,0,1)}
MODES=[('taker',0,1),('entry1',1,1),('entry5',1,5),('both1',2,1),('both5',2,5)]
def execute(t,rule,ts,A,F,S,period,mode,scenario,capital=100):
 a,b=({'early':('2026-06-04','2026-08-01'),'validation':('2026-08-01','2026-09-01'),'later':('2026-09-01','2026-10-11')} if t=='KXBTCPERP' else {'early':('2026-06-04','2026-10-01'),'later':('2026-10-01','2026-10-10')})[period]
 st=np.searchsorted(ts,int(pd.Timestamp(a,tz='UTC').timestamp()));en=np.searchsorted(ts,int(pd.Timestamp(b,tz='UTC').timestamp()));en=min(en,len(ts))
 while en>st and not np.isfinite(A[en-1,0]+A[en-1,1]):en-=1
 if en<=st:return None,None,None
 fr,de,cc,mi,mult=SCEN[scenario];name,mk,ttl=mode;tick=float(old.M[t]['tick_size']);tick=max(tick,.0005) if t=='KXSILVERPERP' else tick
 o,path,dd,att,miss,amb,missing,held=replay(A,S,F,st,en,mk,ttl,fr/1e4,de,cc,mi,fees['taker_fee_rates'][t],fees['maker_fee_rates'][t]*mult,tick,capital,(next(c['tp_bps'] for c in __import__('study').CONFIGS if c['id']==int(rule))/1e4 if t=='KXBTCPERP' else 0.),int(scenario in ['adverse_stop','combined']))
 cols=['signal_i','post_i','entry_i','exit_i','side','qty','entry_price','exit_price','gross','funding','fees','slippage','net','reason','entry_maker','exit_maker','stop','target'];l=pd.DataFrame(o,columns=cols)
 for col in ['signal_i','post_i','entry_i','exit_i']:l[col.replace('_i','_ts')]=[int(ts[int(x)]) for x in l[col]]
 r=dict(ticker=t,rule=rule,period=period,mode=name,scenario=scenario,trades=len(l),net=float(l.net.sum()),gross=float(l.gross.sum()),funding=float(l.funding.sum()),fees=float(l.fees.sum()),friction=float(l.slippage.sum()),dd=dd,attempts=att,expired_or_rejected=miss,ambiguous=amb,missing_held=missing,held=held,wins=int((l.net>0).sum()),maker_exits=int(l.exit_maker.sum()),without_best3=float(l.net.sum()-l.net.nlargest(3).sum()),start=int(ts[st]),end=int(ts[en-1]),capital=capital)
 assert abs(path[-1]-capital-r['net'])<1e-6
 return r,l,np.c_[ts[st:en],path]
if __name__=='__main__':
 tickers=[t for t in old.M if old.M[t]['asset_class'] in ['Metals','Indices'] and (D/(t+'_1m.json.gz')).exists()]+['KXBTCPERP'];results=[];audit=[]
 for t in tickers:
  
  if t!='KXBTCPERP' and not json.load(gzip.open(D/(t+'_1m.json.gz'),'rt'))['candlesticks']:
   audit.append(dict(ticker=t,status='no_minute_history'));continue
  ts,A,F,btc=load(t);audit.append(dict(ticker=t,rows=len(ts),valid=int(np.isfinite(A[:,0]+A[:,1]).sum()),start=int(ts[0]),end=int(ts[-1]),volume_available=True))
  rules=['h_reversal24','m_reversal60','m_reversal240','h_channel48'] if btc is None else ['78','158','237','302','382','474','20']
  for rule in rules:
   S=signals(t,rule,ts,A,btc)
   for mode in MODES:
    for period in (['early','later'] if btc is None else ['early','validation','later']):
     for scenario in SCEN:
      r,l,path=execute(t,rule,ts,A,F,S,period,mode,scenario)
      if r:
       results.append(r);key='_'.join([t,rule,mode[0],period,scenario]);l.to_parquet(P/(key+'.parquet'),index=False)
   print(t,rule,'done',flush=True)
  pd.DataFrame(results).to_csv(P/'results.csv',index=False)
 (P/'data_audit.json').write_text(json.dumps(audit,indent=2))
