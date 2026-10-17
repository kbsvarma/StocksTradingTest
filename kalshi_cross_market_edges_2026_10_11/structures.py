"""Read-only cross-contract executable quote screen, no order client."""
from pathlib import Path
import sys,math,json,gzip,itertools,collections
P=Path(__file__).resolve().parent;ROOT=P.parent;OLD=ROOT/'kalshi_every_window_marketwide_2026_10_10'
sys.path.insert(0,str(OLD))
from core import read,fee,qte,pay,ts,simulate,stats,day
from recurring import vol
def save(name,x):
 p=P/name
 if p.suffix=='.gz':
  with gzip.open(p,'wt') as f:json.dump(x,f,allow_nan=False)
 else:p.write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def leg(m,cs,side,t,mult,delay=False):
 q=qte(cs.get(t),side);n=qte(cs.get(t+60),side,'close' if delay else 'open')
 if not q or not n or not .01<=q[1]<=.99 or q[1]-q[0]>.05+1e-8 or vol(cs,t)<5:return None
 price=max(q[1],n[1]);when=t+(60 if delay else 3)
 if price>min(.99,q[1]+.02)+1e-8 or when>=min(ts(m['close_time']),ts(m['settlement_ts'])):return None
 return dict(ticker=m['ticker'],side=side,entry=price,ask=q[1],fee=fee(price,1,mult),mult=mult,payout=pay(m,side),settled=ts(m['settlement_ts']),entered=when,t=t,cap=min(.99,q[1]+.02))
def low_cost(legs,t,cm,friction=.01):
 vals=[]
 for m,side,mult in legs:
  cs=cm.get(m['ticker'],{});q=qte(cs.get(t),side)
  if not q or not .01<=q[1]<=.99 or q[1]-q[0]>.05+1e-8 or vol(cs,t)<5:return None
  vals.append(q[1]+fee(q[1],1,mult)+friction)
 return sum(vals)
def baskets():
 candidates=collections.defaultdict(list);counts=collections.Counter();exceptions=[]
 def event(g,cm,pools,times):
  # Cache repeated per-contract lookups across strike combinations/scenarios.
  cache={}
  def cheap(legs,t):
   vals=[]
   for m,side,mult in legs:
    k=(m['ticker'],side,t,mult)
    if k not in cache:cache[k]=low_cost([(m,side,mult)],t,cm)
    if cache[k] is None:return None
    vals.append(cache[k])
   return sum(vals)
  for kind,legs,floor in pools:
   possible=[t for t in times if (v:=cheap(legs,t)) is not None and v<floor-1e-8]
   if not possible:continue
   for persist in [0,2]:
    for delay in [False,True]:
     key=kind+f'/persist{persist}/'+('delay60' if delay else 'base')
     for t in possible:
      values=[cheap(legs,t-i*60) for i in range(persist+1)]
      if any(v is None or v>=floor-1e-8 for v in values):continue
      counts[key+'_signals']+=1
      rr=[leg(m,cm.get(m['ticker'],{}),side,t,mult,delay) for m,side,mult in legs]
      if any(r is None for r in rr):continue
      cost=sum(r['entry']+r['fee']+.01 for r in rr)
      if cost>=floor-1e-8:continue
      payout=sum(r['payout'] for r in rr)
      if payout<floor-1e-8:exceptions.append(dict(event=g['event'],kind=kind,payout=payout,floor=floor,legs=rr))
      candidates[key].append(dict(event=g['event'],asset=g['asset'],date=g.get('date',day(t)),owner=g.get('owner',g['event']),entered=max(r['entered'] for r in rr),settled=max(r['settled'] for r in rr),legs=rr,cost=cost,net=payout-cost,payout=payout,floor=floor));break
  counts[g['asset']+'_events']+=1
 for g in read(OLD/'pregame_index.json'):
  fp=OLD/'pregame'/g['asset']/(g['event']+'_quotes.json.gz')
  if not fp.exists():continue
  cm={k:{int(c['end_period_ts']):c for c in v} for k,v in read(fp).items()};legs=[(m,'no',g['mult']) for m in g['markets']]
  # At most one of the matched, named competitors can win this same game.
  event(g,cm,[('sports_no_basket',legs,len(legs)-1)],range((g['start']-1800)//60*60,(g['start']-300)//60*60+1,60))
 base=ROOT/'kalshi_noncrypto_consistency_2026_10_10/financial'
 dirs=[d for d in base.iterdir() if d.is_dir() and (d/'markets.json').exists()]
 for d in dirs:
  groups=collections.defaultdict(list);meta=read(d/'series.json') if (d/'series.json').exists() else {};mult=meta.get('fee_multiplier',1)
  for m in read(d/'markets.json'):groups[m['event_ticker']].append(m)
  for et,items in groups.items():
   fp=d/(et+'_candles.json.gz')
   if not fp.exists():continue
   cm={k:{int(c['end_period_ts']):c for c in v} for k,v in read(fp).items()};eligible=sorted([m for m in items if m.get('strike_type') in ('greater','greater_or_equal') and m.get('floor_strike') is not None and m.get('settlement_ts')],key=lambda m:float(m['floor_strike']));pools=[]
   for lo,hi in itertools.combinations(eligible,2):
    if lo['strike_type']!=hi['strike_type'] or lo['floor_strike']>=hi['floor_strike'] or lo['close_time']!=hi['close_time']:continue
    #Necessary raw-price condition before the fee/volume screen. This does not
    #change eligibility; it avoids iterating millions of ordered quote pairs.
    lc,hc=cm.get(lo['ticker'],{}),cm.get(hi['ticker'],{})
    if not any((lq:=qte(lc[t])) and (hq:=qte(hc[t])) and lq[1]+.02<hq[0] for t in lc.keys()&hc.keys()):continue
    pools.append(('thresholds',[(lo,'yes',mult),(hi,'no',mult)],1))
   tt=sorted(set(t for cs in cm.values() for t in cs));event(dict(event=et,asset=d.name),cm,pools,tt)
 save('structure_candidates.json.gz',candidates);save('structure_counts.json',dict(counts));save('structural_payout_exceptions.json',exceptions)
 # Multiple strike pairs for one event are correlated; keep FIRST eligible
 # event basket, tie-break only by contemporaneous guaranteed surplus.
 result={}
 for key,rows in candidates.items():
  rows=sorted(rows,key=lambda r:(r['entered'],-(r['floor']-r['cost']),r['event']));seen=set();rr=[];active=[];cash=100;daily=collections.defaultdict(float);skip=collections.Counter()
  for r in rows:
   if r['owner'] in seen:continue
   for a in active:
    if a['settled']<=r['entered']:cash+=a['payout'];daily[day(a['settled'])]+=a['net']
   active=[a for a in active if a['settled']>r['entered']];reserve=sum(a['cost'] for a in active);qty=sum(len(a['legs']) for a in active)
   if reserve+r['cost']>25 or qty+len(r['legs'])>25 or daily[day(r['entered'])]-reserve-r['cost'] < -20 or cash-r['cost']<2:skip['risk']+=1;continue
   seen.add(r['owner']);active.append(r);rr.append(r);cash-=r['cost']
  result[key]=dict(baskets=len(rr),raw_pair_candidates=len(rows),net=sum(r['net'] for r in rr),dates=sorted(set(r['date'] for r in rr)),skips=dict(skip),rows=rr)
 save('structure_results.json',result);print('STRUCTURAL', {k:{a:v[a] for a in ['baskets','net','raw_pair_candidates']} for k,v in result.items()},flush=True)
def route():
 cand=collections.defaultdict(list);den=collections.Counter();routequality=collections.Counter()
 for g in read(OLD/'pregame_index.json'):
  if len(g['markets'])!=2:continue
  den[g['date']]+=1;fp=OLD/'pregame'/g['asset']/(g['event']+'_quotes.json.gz')
  if not fp.exists():continue
  cm={k:{int(c['end_period_ts']):c for c in v} for k,v in read(fp).items()}
  for horizon in [30,5]:
   t=(g['start']-60*horizon)//60*60;qs=[qte(cm.get(m['ticker'],{}).get(t)) for m in g['markets']]
   if any(q is None for q in qs):continue
   order=sorted(range(2),key=lambda i:(sum(qs[i]),g['markets'][i]['ticker']),reverse=True)
   for direction,i in [('favorite',order[0]),('underdog',order[1])]:
    for routing in ['yes_only','best_route']:
     # NO opponent includes a possible tie and weakly dominates YES team in
     # ordinary winner settlement; scalar cancellation exceptions audited.
     paths=[(g['markets'][i],'yes')]+([(g['markets'][1-i],'no')] if routing=='best_route' else [])
     known=[]
     for m,side in paths:
      q=qte(cm.get(m['ticker'],{}).get(t),side)
      if q and .01<=q[1]<=.99 and q[1]-q[0]<=.05+1e-8 and vol(cm[m['ticker']],t)>=5:known.append((q[1]+fee(q[1],1,g['mult']),m['ticker'],side,m))
     if not known:continue
     _,_,side,m=min(known,key=lambda x:x[:3])
     for delay in [False,True]:
      r=leg(m,cm[m['ticker']],side,t,g['mult'],delay)
      if r and r['entered']<g['start']:
       r.update(asset=g['asset'],owner=g['owner'],event=g['event'],date=g['date'],original_target=g['markets'][i]['ticker'],original_payout=pay(g['markets'][i],'yes'))
       cand[f'{direction}/pre{horizon}/{routing}/'+('delay60' if delay else 'base')].append(r)
       if r['payout']+1e-8<r['original_payout']:routequality['payout_dominance_exception']+=1
 result={};ledgers={}
 for key,rr in cand.items():
  for co,start,end,days in [('early','2026-08-15','2026-09-11',28),('selection','2026-09-12','2026-09-30',19),('later','2026-10-01','2026-10-09',9)]:
   for stress in [.01,.02]:
    rows,sk=simulate([r for r in rr if start<=r['date']<=end],1,stress);s=stats(rows);s.update(trades_per_day=len(rows)/days,skips=sk,calendar_days=days);name=key+'/'+co+f'/cost{round(stress*100)}';result[name]=s;ledgers[name]=rows
 save('route_results.json',result);save('route_ledgers.json.gz',ledgers);save('route_quality.json',dict(routequality))
 for k,v in result.items():
  if '/base/' in k and k.endswith('/cost1'):print(k,v['trades'],v['net'],round(v['trades_per_day'],2),flush=True)
if __name__=='__main__':baskets();route()
