"""Executable-side paired quote replay. Research only; no account/order code."""
from pathlib import Path
import collections,datetime as dt,math,json,pickle,gzip,itertools,argparse
import numpy as np
P=Path(__file__).resolve().parent
DAYS={'fit':15,'selection':15,'test':20,'supplement':1,'forward':7}
def save(name,x):
 path=P/name
 if path.suffix=='.gz':
  with gzip.open(path,'wt') as f:json.dump(x,f,allow_nan=False)
 else:path.write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def fee(p):return math.ceil(.07*p*(1-p)*10000-1e-9)/10000
def day(t):return dt.datetime.fromtimestamp(t,dt.timezone.utc).date().isoformat()
def quote(m,t,side,field='c'):
 c=m['candles'].get(t)
 if c is None:return None
 try:b,a=float(c['b'+field]),float(c['a'+field])
 except (ValueError,TypeError,KeyError):return None
 if not 0<=b<=a<=1:return None
 return (b,a) if side==1 else (1-a,1-b)
def probability(m,t,model):
 f=m['new_features'].get((m['close']-t)//60)
 if not f or ('received' in f and f['received']-t>35):return None
 if model=='rich':return f.get('prich')
 if 'z' not in f:return None
 return .5*(1+math.erf(f['z']/math.sqrt(2)))
def signal_quote(m,t,side):
 if m['cohort']=='forward':
  f=m['new_features'].get((m['close']-t)//60)
  if f is None or f.get('received',t)-t>35:return None
  return (f['b'],f['a']) if side==1 else (1-f['a'],1-f['b'])
 return quote(m,t,side)
def options(b,e,t,model,friction):
 pb,pe=probability(b,t,model),probability(e,t,model)
 if pb is None or pe is None:return []
 out=[]
 for dr in [1,-1]:
  qs=[signal_quote(b,t,dr),signal_quote(e,t,-dr)]
  if any(q is None or not .01<=q[1]<=.99 or q[1]-q[0]>.05000001 for q in qs):continue
  if any(float((m['new_features'].get((m['close']-t)//60,{}).get('observed_volume') if m['cohort']=='forward' else m['candles'].get(t,{}).get('v')) or 0)<=0 for m in [b,e]):continue
  expected=1+dr*(pb-pe);edge=expected-sum(q[1]+fee(q[1])+friction for q in qs)
  residual=expected-sum(sum(q)/2 for q in qs)
  out.append((edge,dr,qs,residual,expected))
 return sorted(out,reverse=True)
def make_trade(b,e,cfg,delay=False,friction=.01):
 model,floor,schedule,exit_rule=cfg
 rems=range(14,1,-1) if schedule=='first' else [int(schedule)]
 reasons=collections.Counter()
 for rem in rems:
  # Execution cost stresses do not retune the selection threshold.
  t=b['close']-60*rem;oo=options(b,e,t,model,.01)
  if not oo:reasons['missing_or_untradable_signal']+=1;continue
  edge,dr,sq,residual,expected=oo[0]
  if edge<floor:reasons['below_edge']+=1;continue
  entries=[];when=t+(60 if delay else 3)
  for m,side,q in zip([b,e],[dr,-dr],sq):
   nxt=quote(m,t+60,side,'c' if delay else 'o')
   if not nxt or when>=m['close']:break
   cost=round(max(q[1],nxt[1]),6)
   if not .01<=cost<=.99 or cost>q[1]+.02000001:break
   entries.append(cost)
  if len(entries)!=2:reasons['paired_execution_unavailable']+=1;continue
  cost=sum(entries);paid=sum(fee(p) for p in entries);exit_ts=max(b['settled'],e['settled']);out=int(b['result']==dr)+int(e['result']==-dr);legs=2;exit_fee=0;reason='settlement';exit_prices=None;exit_decision=None
  if exit_rule!='settlement':
   for u in range(t+60,b['close'],60):
    if u<=when:continue
    pb,pe=probability(b,u,model),probability(e,u,model)
    qb,qe=signal_quote(b,u,dr),signal_quote(e,u,-dr)
    if qb is None or qe is None:continue
    trigger=(exit_rule=='2min' and u>=t+120)
    if exit_rule=='convergence' and pb is not None and pe is not None:
     trigger=1+dr*(pb-pe)-(sum(qb)+sum(qe))/2<=residual*.5
    if not trigger:continue
    ex=[];ex_t=u+(60 if delay else 3)
    for m,side,q in zip([b,e],[dr,-dr],[qb,qe]):
     nq=quote(m,u+60,side,'c' if delay else 'o')
     if not nq or ex_t>=m['close']:break
     bid=round(min(q[0],nq[0]),6)
     if not 0<bid<1:break
     ex.append(bid)
    if len(ex)==2:out=sum(ex);exit_prices=ex;exit_decision=u;exit_ts=ex_t;exit_fee=sum(fee(p) for p in ex);legs=4;reason=exit_rule;break
  fees=paid+exit_fee
  return dict(close=b['close'],cohort=b['cohort'],tickers=[b['ticker'],e['ticker']],decision=t,entered=when,exit_ts=exit_ts,exit_decision=exit_decision,direction=dr,entry_prices=entries,signal_prices=[q[1] for q in sq],cost=cost,debit=cost+paid+friction*2,exit=out,exit_prices=exit_prices,fee=fees,legs=legs,exit_reason=reason,pnl=out-cost-fees-friction*legs,estimated_edge=edge,settlement_wins=int(b['result']==dr)+int(e['result']==-dr),raw_settlement=[b['result'],e['result']]),dict(reasons)
 return None,dict(reasons)
def portfolio(rows):
 active=[];done=[];cash=100;daily=collections.defaultdict(float);skips=collections.Counter();peak_reserve=0;mincash=100
 for r in sorted(rows,key=lambda x:x['entered']):
  still=[]
  for a in active:
   if a['exit_ts']<=r['entered']:
    cash+=a['debit']+a['pnl'];daily[day(a['exit_ts'])]+=a['pnl'];done.append(a)
   else:still.append(a)
  active=still;reserve=sum(a['debit'] for a in active)
  if (len(active)+1)*2>25 or reserve+r['debit']>25 or daily[day(r['entered'])]-reserve-r['debit'] < -20 or cash-r['debit']<2:
   skips['risk_or_cash']+=1;continue
  active.append(r);cash-=r['debit'];mincash=min(mincash,cash);peak_reserve=max(peak_reserve,reserve+r['debit'])
 done+=active
 return sorted(done,key=lambda x:x['exit_ts']),dict(skips),dict(min_cash=mincash,peak_committed=peak_reserve)
def stats(rows,den,co):
 rr,skips,cash=portfolio(rows);pp=np.array([r['pnl'] for r in rr]);eq=np.r_[0,np.cumsum(pp)];daily=collections.defaultdict(float)
 for r in rr:daily[day(r['exit_ts'])]+=r['pnl']
 return dict(cycles=len(rr),orders=sum(r['legs'] for r in rr),cycles_per_day=len(rr)/DAYS[co],coverage=len(rr)/den if den else 0,denominator=den,net=round(float(pp.sum()),6),drawdown=round(float(np.max(np.maximum.accumulate(eq)-eq)),6),positive=int(sum(pp>0)),negative=int(sum(pp<0)),zero=int(sum(pp==0)),settled_leg_wins=sum(r['settlement_wins'] for r in rr),exit_counts=dict(collections.Counter(r['exit_reason'] for r in rr)),daily=dict(daily),worst_day=round(min(daily.values()),6) if daily else 0,risk_skips=skips,**cash),rr
def configs():return list(itertools.product(['rich','normal'],[0,.03,.06],['first','12','6'],['settlement','2min','convergence']))
def key(c):return '/'.join(map(str,c))
def load():
 mm=pickle.loads((P.parent/'kalshi_every_window_2026_10_03/prepared.pickle').read_bytes())
 if (P/'forward_prepared.pickle').exists():mm+=pickle.loads((P/'forward_prepared.pickle').read_bytes())
 pairs=collections.defaultdict(dict)
 for m in mm:pairs[m['close']][m['asset']]=m
 return [x for _,x in sorted(pairs.items()) if set(x)=={'BTC','ETH'}]
def screen(pairlist,cfg,co,stress,delay):
 rows=[];reasons=collections.Counter();den=0
 for pair in pairlist:
  b,e=pair['BTC'],pair['ETH']
  if b['cohort']!=co:continue
  den+=1;r,why=make_trade(b,e,cfg,delay,stress);reasons.update(why)
  if r:rows.append(r)
 stat,rr=stats(rows,den,co);stat['signal_checks_rejected']=dict(reasons);return stat,rr
def main(mode):
 pairs=load();configs_=configs();out={};ledger={};cohorts=['selection'] if mode=='select' else ['fit','test','supplement','forward']
 if mode=='validate':assert (P/'selection_frozen.json').exists() and (P/'forward_prepared.pickle').exists()
 for cfg in configs_:
  k=key(cfg);out[k]={}
  for co in cohorts:
   out[k][co]={}
   for label,friction,delay in [('base',.01,False),('2c',.02,False),('delay60',.01,True)]:
    stat,rr=screen(pairs,cfg,co,friction,delay);out[k][co][label]=stat
    ledger[k,co,label]=rr
  print(mode,k,{co:out[k][co]['base']['net'] for co in cohorts},flush=True)
 save(mode+'_results.json',out)
 save(mode+'_ledgers.json.gz',{'|'.join(k):v for k,v in ledger.items()})
 if mode=='select':
  passing=[k for k,v in out.items() if all(v['selection'][s]['net']>0 and v['selection'][s]['cycles_per_day']>=20 for s in ['base','2c','delay60'])]
  ranked=sorted(passing,key=lambda k:out[k]['selection']['base']['net'],reverse=True)
  diag=sorted([k for k,v in out.items() if v['selection']['base']['cycles_per_day']>=20],key=lambda k:out[k]['selection']['base']['net'],reverse=True)
  save('selection_frozen.json',dict(trials=len(out),passing=ranked,chosen=ranked[0] if ranked else None,high_frequency_diagnostic_leaders=diag[:5],rule='positive selection net with >=20 cycles/day in base,2c and60s; highest base net'))
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('mode',choices=['select','validate']);main(a.parse_args().mode)
