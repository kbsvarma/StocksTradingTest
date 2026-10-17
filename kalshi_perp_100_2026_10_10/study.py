from pathlib import Path
import sys,json,gzip,math,datetime as dt
import numpy as np,pandas as pd
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P.parent/'kalshi_perp_edge_2026_10_10'))
from core import Q,F,at,mrate,START,SPLIT,END,UTC
TICKERS=['KXBTCPERP','KXETHPERP'];SIG={};PROFILE=['pullback','compression','reclaim']

def signals(t):
 q=Q[t].reindex(np.arange(START-2*86400,END+60,60));x=np.log(q.mid);vol=x.diff().rolling(1440,min_periods=1368).std(ddof=0).shift(1)*np.sqrt(60)
 f=q.mid[q.index%300==0];lp=np.log(f);v=vol.reindex(f.index);r60=lp-lp.shift(12);r5=lp-lp.shift(1)
 fast=lp.ewm(span=72,adjust=False,min_periods=72).mean();slow=lp.ewm(span=288,adjust=False,min_periods=288).mean()
 hi=lp.rolling(72,min_periods=69).max().shift(1);lo=lp.rolling(72,min_periods=69).min().shift(1)
 out={k:{} for k in PROFILE};armed=None
 for s,p,pp,vv,a,b,rr,ret,h,l in zip(f.index,f,lp,v,fast,slow,r60,r5,hi,lo):
  if not np.isfinite(p+vv+a+b+rr+ret+h+l) or vv<=0:continue
  stop=max(.005,1.5*vv)
  if stop>.03:continue
  side=1 if a>b and pp>b and rr<-vv and ret>0 else -1 if a<b and pp<b and rr>vv and ret<0 else 0
  if side:out['pullback'][int(s)]=(side,stop)
  side=(1 if pp>h else -1 if pp<l else 0) if h-l<=2*vv else 0
  if side:out['compression'][int(s)]=(side,stop)
  if armed:
   since,side,anchor,move=armed
   if s-since>1800:armed=None
   elif side*(pp-anchor)>=.25*move:
    out['reclaim'][int(s)]=(side,stop);armed=None
  elif abs(rr)>=2*vv:armed=(s,-1 if rr>0 else 1,pp,abs(rr))
 return out

def run(rule,tickers,lev,start,end,slip=1,delay=60):
 cash=100.;pos=None;pending=None;rows=[];path=[];skips={};cool=0;day=None;daybase=100.;last_eq=100.;maxmargin=0;peak_minute=100.;dd_minute=0.;held_missing_quotes=0
 def skip(k):skips[k]=skips.get(k,0)+1
 def state(stamp):
  if not pos:return cash,False
  r=at(pos['ticker'],stamp)
  if r is None:return None,False
  px=r.bid if pos['side']>0 else r.ask;gross=pos['side']*pos['qty']*(px-pos['price']);coll=pos['margin']+gross+pos['fund']-pos['entry_cost'];mm=mrate(pos['ticker'],pos['side'])*pos['qty']*r.mid
  return cash+gross+pos['fund']-pos['entry_cost']-pos['qty']*px*(.0012+slip/10000),mm/max(coll,1e-9)>=.85
 def close(stamp,reason):
  nonlocal cash,pos,cool
  r=at(pos['ticker'],stamp)
  if r is None:return False
  px=r.bid if pos['side']>0 else r.ask;q=pos['qty'];g=pos['side']*q*(px-pos['price']);fund=-pos['side']*q*sum(rate*mark for ft,(rate,mark) in F[pos['ticker']].items() if pos['entry']<ft<=stamp)
  fees=q*(pos['price']+px)*.0012;friction=q*(pos['price']+px)*slip/10000;net=g+fund-fees-friction
  rows.append(dict(pos,exit=stamp,exit_price=px,reason=reason,gross=g,funding=fund,fees=fees,slippage=friction,net=net));cash+=net;pos=None;cool=stamp+3600;return True
 for stamp in range(start,end+1,60):
  if pos and stamp>pos['entry'] and stamp in F[pos['ticker']]:
   rr,mk=F[pos['ticker']][stamp];pos['fund']-=pos['side']*pos['qty']*rr*mk
  eq,breach=state(stamp)
  if eq is not None:last_eq=eq
  if stamp//86400!=day:day=stamp//86400;daybase=last_eq
  if pending and stamp>=pending['at']:
   if pending['kind']=='close':
    if close(stamp,pending['reason']):pending=None
   else:
    t,side,stop=pending['ticker'],pending['side'],pending['stop'];r=at(t,stamp)
    if r is None:skip('missing_entry_quote')
    elif (r.ask-r.bid)/r.mid>.0005:skip('wide_entry_quote')
    elif cash<20:skip('cash_floor')
    else:
     px=r.ask if side>0 else r.bid;risk=min(2.,cash*.02);costfraction=2*(.0012+slip/10000)+(r.ask-r.bid)/px
     qty=int(min(risk/(px*(stop+costfraction)),cash*.5*lev/px));margin=qty*px/lev;ec=qty*px*(.0012+slip/10000)
     if qty<1:skip('integer_size')
     elif margin<1.3*mrate(t,side)*qty*px:skip('initial_margin')
     elif cash-margin-ec<20:skip('reserve')
     elif max(0,daybase-cash)+qty*px*(stop+costfraction)>6:skip('daily_budget')
     else:
      maxmargin=max(maxmargin,margin);pos={'ticker':t,'side':side,'qty':qty,'entry':stamp,'signal':pending['signal'],'price':px,'stop_frac':stop,'margin':margin,'capital':cash,'entry_cost':ec,'fund':0.,'estimated_stop_risk':qty*px*(stop+costfraction),'day_loss_before_entry':max(0,daybase-cash)}
    pending=None
  if pos:
   eq,breach=state(stamp);r=at(pos['ticker'],stamp)
   if eq is not None:last_eq=eq
   if r is not None and not pending:
    mark=r.bid if pos['side']>0 else r.ask;ret=pos['side']*(mark/pos['price']-1);reason=''
    if breach:reason='margin_buffer'
    elif ret<=-pos['stop_frac']:reason='stop'
    elif ret>=2*pos['stop_frac']:reason='target'
    elif stamp-pos['entry']>=12*3600:reason='time'
    if reason:pending={'kind':'close','at':stamp+delay,'reason':reason}
  if stamp%300==0 and stamp<end-12*3600 and not pos and not pending and stamp>=cool:
   for t in tickers:
    if stamp in SIG[t][rule]:
     side,stop=SIG[t][rule][stamp];pending={'kind':'open','at':stamp+delay,'signal':stamp,'ticker':t,'side':side,'stop':stop};break
  eq,_=state(stamp)
  if eq is not None:
   peak_minute=max(peak_minute,eq);dd_minute=max(dd_minute,peak_minute-eq)
   if stamp%3600==0:path.append({'t':stamp,'equity':eq})
  elif pos:held_missing_quotes+=1
 if pos:
  if not close(end,'sample_end'):raise RuntimeError('missing final quote')
 path.append({'t':end,'equity':cash});values=np.array([100.]+[r['equity'] for r in path]);peak=np.maximum.accumulate(values)
 wk={};prev=100.
 for p in path:
  d=dt.datetime.fromtimestamp(p['t'],UTC).date();w=(d-dt.timedelta(days=d.weekday())).isoformat();wk[w]=wk.get(w,0)+p['equity']-prev;prev=p['equity']
 return {'trades':len(rows),'wins':sum(r['net']>0 for r in rows),'losses':sum(r['net']<=0 for r in rows),'net':cash-100,'drawdown':max(dd_minute,peak_minute-cash),'hourly_drawdown':float(max(peak-values)),'held_missing_quote_minutes':held_missing_quotes,'max_trade_loss':min(0,min((r['net'] for r in rows),default=0)),'fees':sum(r['fees'] for r in rows),'funding':sum(r['funding'] for r in rows),'without_best3':sum(r['net'] for r in rows)-sum(sorted((r['net'] for r in rows),reverse=True)[:3]),'margin_exits':sum(r['reason']=='margin_buffer' for r in rows),'max_posted_margin':maxmargin,'weekly':wk,'positive_weeks':sum(v>0 for v in wk.values()),'active_weeks':sum(abs(v)>1e-8 for v in wk.values()),'skips':skips},rows,path

if __name__=='__main__':
 SIG={t:signals(t) for t in TICKERS};res={};ledger={};paths={}
 for rule in PROFILE:
  for lab,tks in [('BTC',TICKERS[:1]),('ETH',TICKERS[1:]),('joint',TICKERS)]:
   for lev,slip,delay in [(1,1,60),(2,1,60),(3,1,60),(2,5,60),(2,1,120)]:
    for name,start,end in [('earlier',START,SPLIT),('later',SPLIT,END)]:
     k=f'{rule}/{lab}/L{lev}/slip{slip}/delay{delay}/{name}';r,l,p=run(rule,tks,lev,start,end,slip,delay);res[k]=r;ledger[k]=l;paths[k]=p
     print(k,r['trades'],round(r['net'],4),round(r['drawdown'],4),flush=True)
 (P/'results.json').write_text(json.dumps(res,indent=2)+'\n')
 for n,x in [('ledgers',ledger),('paths',paths),('signals',SIG)]:
  with gzip.open(P/(n+'.json.gz'),'wt') as f:json.dump(x,f)
