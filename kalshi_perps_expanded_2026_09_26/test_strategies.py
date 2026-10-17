import json,math,statistics,collections,datetime,bisect
from pathlib import Path
P=Path(__file__).resolve().parent;D=P/'data';OUT=P/'trades';OUT.mkdir(exist_ok=True)
def load(f):return json.loads(f.read_text())
def ts(s):return int(datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
def quote(c):
 try:b=float(c['bid']['close']);a=float(c['ask']['close'])
 except (KeyError,TypeError,ValueError):return None
 return (b,a,(b+a)/2) if 0<b<=a<1e8 else None
M={m['ticker']:m for m in load(D/'markets.json')['markets']};C={t:{c['end_period_ts']:c for c in load(D/(t+'_candles.json'))['candlesticks']} for t in M};Q={t:{s:quote(c) for s,c in cs.items()} for t,cs in C.items()};F={t:sorted((ts(f['funding_time']),float(f['funding_rate']),float(f['mark_price'])) for f in load(D/(t+'_funding.json'))['funding_rates']) for t in M};END=min(load(D/'receipt.json')['end_ts'],1790467200);SPLIT=END-14*86400

def history(t,T):
 qs=[Q[t].get(T-i*3600) for i in range(73)]
 if not all(qs):return None
 if (qs[0][1]-qs[0][0])/qs[0][2]>.002 or float(C[t][T].get('volume') or 0)<=0:return None
 return [q[2] for q in qs]
def sign(x):return 1 if x>0 else -1 if x<0 else 0
def signal(t,T,rule):
 h=history(t,T)
 if not h:return None
 a=math.log(h[0]/h[24]);b=math.log(h[0]/h[72]);six=math.log(h[0]/h[6]);sd=statistics.pstdev([math.log(h[i]/h[i+1]) for i in range(72)])
 if rule=='trend24_72':s=sign(a) if a*b>0 else 0
 elif rule=='breakout24':s=1 if h[0]>max(h[1:25]) else -1 if h[0]<min(h[1:25]) else 0
 elif rule=='shock_reversal':s=-sign(six) if abs(six)>sd*math.sqrt(6) else 0
 elif rule=='funding_fade':
  fs=[r for ft,r,m in F[t] if ft<T and ft>=T-86400*2];s=-sign(fs[-1]) if fs and abs(fs[-1])>=.0001 else 0
 return [(t,s,50)] if s else None

def fill(T,hold,legs):
 et=T+3600;xt=et+hold*3600
 if xt>END:return None
 rr=[]
 for t,s,budget in legs:
  e=Q[t].get(et);x=Q[t].get(xt)
  if not e or not x:return None
  ep=e[1] if s==1 else e[0];xp=x[0] if s==1 else x[1];n=math.floor(budget/ep)
  if n<1:return None
  rate=.000072 if M[t]['asset_class']=='Metals' else .0012
  fee=n*(ep+xp)*rate;fund=sum(-s*n*m*r for ft,r,m in F[t] if et<ft<=xt);gross=s*n*(xp-ep)
  st=2*max(n*float(M[t]['tick_size']),n*ep*.0001);st5=2*max(n*float(M[t]['tick_size']),n*ep*.0005)
  rr.append(dict(ticker=t,side=s,count=n,entry=ep,exit=xp,fees=fee,funding=fund,gross=gross,stress=st,stress5=st5))
 marks=[];missing=0
 for stamp in range(et,xt+1,3600):
  v=0
  for leg in rr:
   t=leg['ticker'];q=Q[t].get(stamp)
   if not q:missing+=1;break
   xp=q[0] if leg['side']==1 else q[1];n=leg['count'];rate=.000072 if M[t]['asset_class']=='Metals' else .0012
   fund=sum(-leg['side']*n*m*r for ft,r,m in F[t] if et<ft<=stamp)
   v+=leg['side']*n*(xp-leg['entry'])-n*(leg['entry']+xp)*rate+fund-leg['stress']
  else:marks.append(v)
 return {'signal':T,'entry_ts':et,'exit_ts':xt,'net':sum(l['gross']-l['fees']+l['funding']-l['stress'] for l in rr),'stress5':sum(l['gross']-l['fees']+l['funding']-l['stress5'] for l in rr),'fees':sum(l['fees'] for l in rr),'funding':sum(l['funding'] for l in rr),'legs':rr,'marks':marks,'missing_marks':missing}

def run(name,hold,offset,fun,times):
 rows=[];skips=0
 for T in times:
  if T%(hold*3600)!=offset*3600 or T+3600+hold*3600>END:continue
  legs=fun(T)
  if not legs:continue
  r=fill(T,hold,legs)
  if r:rows.append(r)
  else:skips+=1
 eq=peak=dd=0
 for r in rows:
  for v in r.pop('marks'):peak=max(peak,eq+v);dd=max(dd,peak-eq-v)
  eq+=r['net']
 (OUT/(name+'_o'+str(offset)+'.json')).write_text(json.dumps(rows))
 return dict(name=name,offset=offset,trades=len(rows),days=len({r['signal']//86400 for r in rows}),net=sum(r['net'] for r in rows),stress5=sum(r['stress5'] for r in rows),early=sum(r['net'] for r in rows if r['signal']<SPLIT),recent14=sum(r['net'] for r in rows if r['signal']>=SPLIT),recent_trades=sum(r['signal']>=SPLIT for r in rows),dd=dd,fees=sum(r['fees'] for r in rows),funding=sum(r['funding'] for r in rows),missing_execution=skips,missing_marks=sum(r['missing_marks'] for r in rows))
rows=[]
for t in M:
 for rule,hold in [('trend24_72',24),('breakout24',6),('shock_reversal',6),('funding_fade',24)]:
  for o in range(0,24,6) if hold==24 else range(6):rows.append(run(t+'_'+rule,hold,o,lambda T:signal(t,T,rule),sorted(C[t])))
 print(t,'done',flush=True)
pairs=[('BTC','ETH'),('BTC','SOL'),('ETH','SOL'),('BCH','LTC'),('GOLD','SILVER')]
for a,b in pairs:
 a='KX'+a+'PERP';b='KX'+b+'PERP'
 def pair(T):
  ha=history(a,T);hb=history(b,T)
  if not ha or not hb:return None
  rel=[math.log(x/y) for x,y in zip(ha,hb)];move=rel[0]-rel[6];sd=statistics.pstdev([rel[i]-rel[i+1] for i in range(72)])
  return [(a,-sign(move),25),(b,sign(move),25)] if abs(move)>sd*math.sqrt(6) else None
 for o in range(6):rows.append(run(a+'_'+b+'_relative_reversal',6,o,pair,sorted(set(C[a])&set(C[b]))))
crypto=[t for t in M if M[t]['asset_class']!='Metals']
times=sorted(set.union(*(set(C[t]) for t in crypto)))
for direction in [1,-1]:
 def cross(T):
  eligible=[]
  for t in crypto:
   h=history(t,T)
   if h:eligible.append((math.log(h[0]/h[24]),t))
  if len(eligible)<4:return None
  eligible.sort();return [(eligible[-1][1],direction,25),(eligible[0][1],-direction,25)]
 for o in range(0,24,6):rows.append(run('crypto_cross_'+('momentum' if direction==1 else 'reversal'),24,o,cross,times))
(P/'results.json').write_text(json.dumps(rows,indent=2));print('COMPLETE',len(rows))
