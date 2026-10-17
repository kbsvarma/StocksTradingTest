from core import *
from collections import defaultdict

def pair_indicators():
 h=pd.DataFrame({t:q.mid[q.index%3600==0] for t,q in Q.items()});h=h.reindex(np.arange(h.index.min(),h.index.max()+1,3600));x=np.log(h['KXBTCPERP']);y=np.log(h['KXETHPERP']);rx=x.diff();ry=y.diff()
 beta=(ry.rolling(336,min_periods=320).cov(rx)/rx.rolling(336,min_periods=320).var()).shift(1).clip(.5,2)
 mx=x.rolling(168,min_periods=160).mean().shift(1);my=y.rolling(168,min_periods=160).mean().shift(1)
 vx=x.rolling(168,min_periods=160).var(ddof=0).shift(1);vy=y.rolling(168,min_periods=160).var(ddof=0).shift(1);cov=x.rolling(168,min_periods=160).cov(y,ddof=0).shift(1)
 sd=np.sqrt((vy+beta**2*vx-2*beta*cov).clip(1e-14));z=((y-beta*x)-(my-beta*mx))/sd
 fs={}
 for t in Q:
  fs[t]=pd.Series({stamp:sum(r for ft,(r,_) in F[t].items() if stamp-7*86400<ft<=stamp) for stamp in h.index})
 carry=beta*fs['KXBTCPERP']-fs['KXETHPERP']
 return {int(stamp):{'beta':float(beta.loc[stamp]),'z':float(z.loc[stamp]),'carry':float(carry.loc[stamp]),'mx':float(mx.loc[stamp]),'my':float(my.loc[stamp]),'vx':float(vx.loc[stamp]),'vy':float(vy.loc[stamp]),'cov':float(cov.loc[stamp]),'x':float(x.loc[stamp]),'y':float(y.loc[stamp])} for stamp in h.index if np.isfinite(beta.loc[stamp]+z.loc[stamp]+carry.loc[stamp])}

def pair(profile,lev,slip,start=START):
 e=Engine(slip);pending=None;cool=0
 for stamp in range(start,END+1,60):
  if pending and stamp>=pending['at']:
   if pending['kind']=='close':
    if e.close(stamp,pending['reason']):pending=None;cool=stamp+3600
   else:
    i=pending['i'];side=pending['side'];b=1 if profile=='carry_hold' else i['beta'];e.open(stamp,[('KXETHPERP',side,1/(1+b)),('KXBTCPERP',-side,b/(1+b))],lev,dict(i,profile=profile,signal=pending['at']-60,side=side));pending=None
  breach=e.tick(stamp)
  if breach and e.pos and not pending:pending={'at':stamp+60,'kind':'close','reason':'margin_buffer'}
  if stamp%3600 or stamp>=END-60 or pending or stamp<cool:continue
  i=PI.get(stamp)
  if not i:continue
  if e.pos:
   b=e.pos['meta']['beta'];s=e.pos['meta']['side'];sd=math.sqrt(max(1e-14,i['vy']+b*b*i['vx']-2*b*i['cov']));z=((i['y']-b*i['x'])-(i['my']-b*i['mx']))/sd
   reason=''
   if profile!='carry_hold':
    if s*z>=0:reason='mean'
    elif s*z<=-4:reason='residual_stop'
    elif stamp-e.pos['entry']>=7*86400:reason='time'
   if reason:pending={'at':stamp+60,'kind':'close','reason':reason}
  else:
   side=0
   if profile=='carry_hold':side=1
   elif i['z']<=-2 and (profile!='z2_carry' or i['carry']>0):side=1
   elif profile=='z2_both' and i['z']>=2:side=-1
   if side:pending={'at':stamp+60,'kind':'open','i':i,'side':side}
 if e.pos:e.close(END,'sample_end');e.tick(END)
 return e

def daily_signals(t):
 pair='BTC-USD' if 'BTC' in t else 'ETH-USD';rr=json.load(open(D/(pair+'_86400.json')));d=pd.DataFrame(rr,columns=['start','low','high','open','close','volume']).set_index('start');px=d.close.to_numpy();signals=[];a=b=0
 for i,c in enumerate(px):
  if i>=120:
   if c>=max(px[i-120:i]):a=1
   if c<=min(px[i-60:i]):a=0
  if i>=180:
   if c>=max(px[i-180:i]):b=1
   if c<=min(px[i-90:i]):b=0
  vote=int(i>=252 and sum(c>px[i-k] for k in (60,120,252))>=2)
  signals.append(int(a+b+vote>=2) if i>=252 else 0)
 vol=pd.Series(np.log(px)).diff().rolling(30,min_periods=30).std(ddof=0).to_numpy()*math.sqrt(365)
 return {int(k)+86400:{'long':int(s),'vol':float(v),'close':float(c)} for k,s,v,c in zip(d.index,signals,vol,px)}

def directional(t,rule,lev,slip,start=JULY):
 e=Engine(slip);pending=None;cool=0
 for stamp in range(start,END+1,60):
  if pending and stamp>=pending['at']:
   if e.close(stamp,pending['reason']):pending=None;cool=stamp+86400
  breach=e.tick(stamp)
  if breach and e.pos and not pending:pending={'at':stamp+60,'reason':'margin_buffer'}
  if stamp%86400!=300 or pending or stamp>=END-60 or stamp<cool:continue
  info=DS[t].get(stamp-300)
  if not info:continue
  signal=1 if rule=='buyhold' else info['long']
  if signal and not e.pos:
   l=min(1.5,.30/info['vol']) if lev=='vol30' else lev;e.open(stamp,[(t,1,1.)],l,dict(info,profile=rule,signal=stamp-300,lev=l));e.tick(stamp)
  elif not signal and e.pos:e.close(stamp,'signal');e.tick(stamp)
 if e.pos:e.close(END,'sample_end');e.tick(END)
 return e

if __name__=='__main__':
 import sys
 mode=sys.argv[1] if len(sys.argv)>1 else 'all';results={};ledgers={};paths={}
 if mode in ('all','pair'):
  PI=pair_indicators()
  for profile in ('carry_hold','z2_long','z2_carry','z2_both'):
   for lev in (1,1.5,2,3):
    for slip in (1,5):
     for label,start in [('full',START),('later_restart',SPLIT)]:
      k=f'{profile}/L{lev}/slip{slip}/{label}';e=pair(profile,lev,slip,start);m=metrics(e.path,e.rows);m['skips']=e.skips;results[k]=m;ledgers[k]=e.rows;paths[k]=e.path
      print(k,{a:m[a] for a in ('trades','net','drawdown_pct','funding','margin_stops','late')},flush=True)
 if mode in ('all','trend'):
  DS={t:daily_signals(t) for t in Q}
  save('daily_signals.json',DS)
  for t in Q:
   for rule in ('ensemble','buyhold'):
    for lev in (1,1.5,2,3,'vol30'):
     for slip in (1,5):
      k=f'{t}/{rule}/L{lev}/slip{slip}';e=directional(t,rule,lev,slip);m=metrics(e.path,e.rows);m['skips']=e.skips;results[k]=m;ledgers[k]=e.rows;paths[k]=e.path
      print(k,{a:m[a] for a in ('trades','net','drawdown_pct','funding','margin_stops','late')},flush=True)
 save(mode+'_results.json',results);save(mode+'_ledgers.json.gz',ledgers);save(mode+'_paths.json.gz',paths)
