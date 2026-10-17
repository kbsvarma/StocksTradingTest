from engine import *

def runpair(a,b,lev,slip,start,end,delay=60):
 qa,fa=load(a,60);qb,fb=load(b,60)
 if not qa or not qb:return {'trades':0,'net':0,'status':'no_history'},[],[]
 begin=max(min(qa),min(qb));end=min(end,max(qa),max(qb));start=max(start,begin)
 idx=np.arange(math.ceil(begin/300)*300,end+300,300);rat=pd.Series({s:math.log(qa[s].mid/qb[s].mid) for s in idx if s in qa and s in qb}).reindex(idx);mu=rat.rolling(576,min_periods=519).mean().shift(1);sd=rat.rolling(576,min_periods=519).std(ddof=0).shift(1)
 ind={int(s):(float(mu.loc[s]),float(sd.loc[s])) for s in idx if np.isfinite(mu.loc[s]+sd.loc[s]) and sd.loc[s]>0}
 qs={a:qa,b:qb};fs={a:fa,b:fb};cash=100.;pos=None;pending=None;rows=[];path=[];peak=100.;dd=0;cool=0;day=None;daybase=100.;last=100.;skips=Counter();missing=0
 def values(s):
  if not pos:return cash,False,[]
  vals=[];eq=cash;breach=False
  for l in pos['legs']:
   t=l['ticker'];q=qs[t].get(s)
   if not q:return None,False,[]
   px=q.bid if l['side']>0 else q.ask;n=l['qty'];g=l['side']*n*(px-l['price']);fund=-l['side']*n*sum(rate*mark for ft,(rate,mark) in fs[t].items() if pos['entry']<ft<=s);fee=n*(px+l['price'])*FEES[t];cost=n*(px+l['price'])*slip/10000
   coll=l['margin']+g+fund-n*l['price']*(FEES[t]+slip/10000);breach|=mr(t,l['side'])*n*q.mid/max(coll,1e-9)>=.85;eq+=g+fund-fee-cost;vals.append(dict(l,exit_price=px,gross=g,funding=fund,fees=fee,slippage=cost,net=g+fund-fee-cost))
  return eq,breach,vals
 def close(s,reason):
  nonlocal cash,pos,cool
  eq,_,vals=values(s)
  if eq is None:return False
  rows.append(dict(pos,exit=s,legs=vals,net=eq-cash,reason=reason));cash=eq;pos=None;cool=s+3600;return True
 for s in range(math.ceil(start/60)*60,end+1,60):
  eq,breach,_=values(s)
  if eq is not None:last=eq
  d=dt.datetime.fromtimestamp(s,ET)
  if d.date()!=day:day=d.date();daybase=last
  if pending and s>=pending['at']:
   if pending['kind']=='close':
    if close(s,pending['reason']):pending=None
   else:
    x,y=qa.get(s),qb.get(s)
    if not x or not y:skips['entry_missing']+=1
    elif max((x.ask-x.bid)/x.mid,(y.ask-y.bid)/y.mid)>.001:skips['spread']+=1
    else:
     risk=min(2,.02*cash);cost=2*(FEES[a]+FEES[b])+4*slip/10000+(x.ask-x.bid)/x.mid+(y.ask-y.bid)/y.mid;stop=2*pending['sd'];notional=min(.25*cash*lev,risk/(stop+cost));side=pending['side'];legs=[]
     for t,sign,q in [(a,side,x),(b,-side,y)]:
      px=q.ask if sign>0 else q.bid;n=int(notional/px);legs.append({'ticker':t,'side':sign,'price':px,'qty':n,'margin':n*px/lev})
     used=sum(l['margin'] for l in legs);ec=sum(l['qty']*l['price']*(FEES[l['ticker']]+slip/10000) for l in legs);estimated=max(l['qty']*l['price'] for l in legs)*(stop+cost)
     if any(l['qty']<1 for l in legs):skips['size']+=1
     elif any(l['margin']<1.3*mr(l['ticker'],l['side'])*l['qty']*l['price'] for l in legs):skips['margin']+=1
     elif cash-used-ec<20:skips['reserve']+=1
     elif max(0,daybase-cash)+estimated>6:skips['daily']+=1
     else:pos={'entry':s,'signal':pending['signal'],'capital':cash,'mean':pending['mean'],'sd':pending['sd'],'legs':legs,'estimated_risk':estimated,'day_loss':max(0,daybase-cash)}
    pending=None
  if pos and not pending:
   eq,breach,_=values(s)
   if eq is not None:
    z=(math.log(qa[s].mid/qb[s].mid)-pos['mean'])/pos['sd'];reason=''
    if breach:reason='margin_buffer'
    elif eq-cash<=-pos['estimated_risk']:reason='dollar_stop'
    elif abs(z)>=4:reason='z4'
    elif abs(z)<=.5:reason='mean'
    elif s-pos['entry']>=86400:reason='time'
    if reason:pending={'kind':'close','at':s+delay,'reason':reason}
  if not pos and not pending and s>=cool and s%300==0 and s in ind and s in qa and s in qb and d.weekday()<5:
   # Both legs must have observed trading activity. No weekend stale-price edge.
   if qa[s].vol<=0 or qb[s].vol<=0:continue
   mean,st=ind[s];z=(math.log(qa[s].mid/qb[s].mid)-mean)/st
   if abs(z)>=2:pending={'kind':'open','at':s+delay,'signal':s,'side':-1 if z>0 else 1,'mean':mean,'sd':st}
  eq,_,_=values(s)
  if eq is not None:
   peak=max(peak,eq);dd=max(dd,peak-eq)
   if s%3600==0:path.append({'t':s,'equity':eq})
  elif pos:missing+=1
 if pos:
  last=max(s for s in qa.keys()&qb.keys() if pos['entry']<s<=end);close(last,'sample_end')
 path.append({'t':end,'equity':cash})
 return {'status':'ok','start':start,'end':end,'trades':len(rows),'wins':sum(r['net']>0 for r in rows),'net':cash-100,'drawdown':max(dd,peak-cash),'without_best3':sum(r['net'] for r in rows)-sum(sorted((r['net'] for r in rows),reverse=True)[:3]),'fees':sum(l['fees'] for r in rows for l in r['legs']),'funding':sum(l['funding'] for r in rows for l in r['legs']),'missing_held_marks':missing,'margin_exits':sum(r['reason']=='margin_buffer' for r in rows),'skips':dict(skips)},rows,path

if __name__=='__main__':
 res={};led={};paths={}
 for a,b in [('KXGOLDPERP','KXSILVERPERP'),('KXPLATINUMPERP','KXPALLADIUMPERP')]:
  for name,s,e in [('early',START,split(a)),('later',split(a),END),('full',START,END)]:
   for lev,slip,delay in [(2,1,60),(2,5,60),(2,1,120)]:
    key=f'{a}-{b}/{name}/L{lev}/slip{slip}/delay{delay}';r,l,p=runpair(a,b,lev,slip,s,e,delay);res[key]=r;led[key]=l;paths[key]=p;print(key,r['trades'],round(r['net'],4),round(r['drawdown'],4),flush=True)
 save('pair_results.json',res);save('pair_ledgers.json.gz',led);save('pair_paths.json.gz',paths)
