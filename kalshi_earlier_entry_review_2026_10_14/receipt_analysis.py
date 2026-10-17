"""Contemporaneous signal and depth analysis; no live imports or writes."""
from pathlib import Path
import json,gzip,sys,collections as C,statistics,math
from decimal import Decimal as D,ROUND_CEILING
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'production_source'))
from lanes import lane_for,Z2,Z3
from price_rules import limit_price
def read(n):
 with gzip.open(P/(n+'.json.gz'),'rt') as f:return json.load(f)
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
ORDERS=read('orders');EVENTS=read('events');OBS=read('observer');ATTEMPTS=read('attempts')
def historical_threshold(f):
 if 'required_z' in f:return f['required_z']
 rule=lane_for(f)
 if f['asset']=='BTC' and f.get('lane',Z2)==Z2 and f.get('decision_ts',0)<1791994560:return 2.
 return rule.threshold(f['remaining_minutes'])
def gate(f):
 try:
  rule=lane_for(f);rem=f['remaining_minutes']
  if not rule.min_minutes<=rem<=rule.max_minutes:return 'time'
  ask,bid=f['signal_ask'],f['signal_bid']
  if not float(rule.floor)-1e-8<=ask<float(rule.ceiling)-1e-8:return 'price'
  if ask-bid>.02000001:return 'spread'
  if 'z' not in f:return 'missing_z'
  if f['z']<historical_threshold(f):return 'z'
  return 'qualified'
 except (KeyError,ValueError,TypeError):return 'missing'
def book(levels,cap,floor,n=5):
 need=n;cost=0.;under=False
 for p,q in sorted((1-float(p),float(q)) for p,q in levels):
  if q<=0:continue
  if p<floor-1e-8:under=True
  if p>cap+1e-8:break
  take=min(q,need);cost+=take*p;need-=take
  if need<1e-8:break
 return dict(enough=need<1e-8 and not under,depth=n-need,average=cost/(n-need) if need<n else None,below_floor=under)
def fee(price,quantity=5):
 p=D(str(price));q=D(str(quantity));cost=p*q;raw=(D('.07')*cost*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING)
 return float((cost+raw).quantize(D('.0001'),rounding=ROUND_CEILING)-cost)
def summary(rows):
 return dict(trades=len(rows),wins=sum(r['won'] for r in rows),losses=sum(not r['won'] for r in rows),net=round(sum(r['net'] for r in rows),6))
def dist(xs):
 xs=sorted(x for x in xs if x is not None and math.isfinite(x))
 return dict(n=len(xs),mean=statistics.mean(xs) if xs else None,median=statistics.median(xs) if xs else None,p95=xs[int(.95*(len(xs)-1))] if xs else None,max=max(xs) if xs else None)
events=C.defaultdict(list);obs=C.defaultdict(list);attempts=C.defaultdict(list)
for f in EVENTS:events[f['ticker']].append(f)
for f in OBS:obs[f['ticker']].append(f)
for f in ATTEMPTS:attempts[f['ticker']].append(f)
rows=[]
for o in ORDERS:
 if o['status']!='settled':continue
 s=o['signal'];T=s['decision_ts'];created=o['created_ts'];ticker=o['ticker'];entry=float(o['entry']);n=float(o['filled']);won=o['side']==o['result']
 assert abs(float(o['cost'])+float(o['fees'])+float(o['pnl'])-n*won)<1e-8
 before=[f for f in events[ticker] if f.get('decision_ts',float('inf'))<T and f.get('observed_ts',f['_saved_ts'])<created]
 # First observed completed-minute evaluation per lane, not best later revision.
 first={}
 for f in sorted(before,key=lambda f:f.get('observed_ts',f['_saved_ts'])):first.setdefault((f['decision_ts'],f.get('lane',Z2)),f)
 before=list(first.values());qualified=sorted([f for f in before if gate(f)=='qualified'],key=lambda f:(f['decision_ts'],f.get('lane',Z2)!=Z2))
 earlier_same=[f for f in qualified if f['side']==o['side']]
 cheaper=[f for f in before if f.get('side')==o['side'] and f.get('signal_ask',1)<entry-1e-8]
 within_band=[f for f in cheaper if 'signal_ask' in f and float(lane_for(f).floor)-1e-8<=f['signal_ask']<float(lane_for(f).ceiling)-1e-8 and f['signal_ask']-f.get('signal_bid',0)<=.02000001 and lane_for(f).min_minutes<=f['remaining_minutes']<=lane_for(f).max_minutes]
 supported=[];sampled=0
 for f in qualified:
  lane=f.get('lane',Z2);rule=lane_for(f);cap=float(limit_price(f)[1]);dt=f['decision_ts'];samples=[]
  for e in events[ticker]+[x['signal'] for x in attempts[ticker] if 'signal' in x]:
   if e.get('decision_ts')!=dt or e.get('lane',Z2)!=lane or e.get('side')!=f['side']:continue
   for tk,bk in [('book_observed_ts','book_levels'),('execution_book_ts','execution_book_levels')]:
    if tk in e and bk in e:samples.append(dict(ts=e[tk],levels=e[bk],source=tk))
  for e in obs[ticker]:
   if e.get('decision_ts')==dt and e.get('lane',Z2)==lane and 'levels' in e:samples.append(dict(ts=e['observed_ts'],levels=e['levels'],source='observer'))
  for b in sorted(samples,key=lambda x:x['ts']):
   if not f.get('observed_ts',dt)<=b['ts']<min(created,dt+25):continue
   sampled+=1;q=book(b['levels'],cap,float(rule.floor),5)
   if q['enough']:
    supported.append(dict(signal=f,book_ts=b['ts'],source=b['source'],cap=cap,**q));break
 first_book=min(supported,key=lambda x:(x['book_ts'],x['signal'].get('lane',Z2)!=Z2)) if supported else None
 first_same=next((x for x in supported if x['signal']['side']==o['side']),None)
 actual_rule=lane_for(o);cap=float(o['favorite_limit']);pre=book(s.get('book_levels',[]),cap,float(actual_rule.floor),n);fin=book(s.get('execution_book_levels',[]),cap,float(actual_rule.floor),n)
 r=dict(ticker=ticker,asset=o['asset'],lane=o.get('lane',Z2),side=o['side'],won=won,quantity=n,entry=entry,net=float(o['pnl']),fees=float(o['fees']),cost=float(o['cost']),created=created,decision_ts=T,rem=s['remaining_minutes'],z=s['z'],required_z=historical_threshold(s),signal_ask=s['signal_ask'],earlier_checks=len(before),earlier_qualified=len(qualified),earlier_same_qualified=len(earlier_same),earlier_qualified_minutes=sorted({f['decision_ts'] for f in qualified}),earlier_cheaper=len(cheaper),cheaper_gate_counts=dict(C.Counter(gate(f) for f in cheaper)),earlier_cheaper_in_band=len(within_band),within_band_gate_counts=dict(C.Counter(gate(f) for f in within_band)),first_qualified=qualified[0] if qualified else None,first_qualified_same=earlier_same[0] if earlier_same else None,earlier_depth_samples=sampled,first_supported=first_book,first_same_supported=first_same,pre_book=pre,final_book=fin,latency=dict(decision_to_intent=created-T,signal_observed_to_intent=created-s.get('observed_ts',created),prebook_to_intent=created-s['book_observed_ts'] if 'book_observed_ts' in s else None,finalbook_to_intent=created-s['execution_book_ts'] if 'execution_book_ts' in s else None),execution_check=s.get('execution_check_number'),attempt_number=o.get('attempt_number'))
 for name,a,b in [('qualified_to_submit','execution_submit_ts','signal_qualified_ts'),('decision_to_submit','execution_submit_ts','decision_ts'),('submit_to_ack','execution_ack_ts','execution_submit_ts'),('finalbook_to_submit','execution_submit_ts','execution_book_ts')]:
  r['latency'][name]=s[a]-s[b] if a in s and b in s else None
 for prefix,b in [('pre',pre),('final',fin)]:
  r[prefix+'_book_fill_difference_cents']=100*(entry-b['average']) if b['enough'] else None
 r['signal_fill_difference_cents']=100*(entry-s['signal_ask'])
 if first_book:
  f=first_book['signal'];px=first_book['average'];counterwon=f['side']==o['result'];r['earliest_supported_counterfactual']=dict(entry=px,won=counterwon,net=n*(counterwon-px)-fee(px,n),same_side=f['side']==o['side'],minutes_earlier=(T-f['decision_ts'])/60,price_saving_cents=100*(entry-px),net_delta=n*(counterwon-px)-fee(px,n)-float(o['pnl']))
 rows.append(r)
out=dict(total=summary(rows),by_asset={a:summary([r for r in rows if r['asset']==a]) for a in sorted({r['asset'] for r in rows})},cohorts={})
for name,rr in [('all',rows),('winners',[x for x in rows if x['won']]),('losers',[x for x in rows if not x['won']]),('after_last_ops_release',[x for x in rows if x['created']>=1792002480])]:
 supported=[x for x in rr if x['first_supported']];counter=[x['earliest_supported_counterfactual'] for x in supported]
 out['cohorts'][name]=dict(**summary(rr),any_earlier_qualified=sum(x['earlier_qualified']>0 for x in rr),earlier_same_qualified=sum(x['earlier_same_qualified']>0 for x in rr),earlier_cheaper=sum(x['earlier_cheaper']>0 for x in rr),earlier_cheaper_in_band=sum(x['earlier_cheaper_in_band']>0 for x in rr),earlier_book_supported=len(supported),first_book_cheaper=sum(x['price_saving_cents']>1e-8 for x in counter),earliest_supported_net_delta=sum(x['net_delta'] for x in counter),earliest_supported_side_changes=sum(not x['same_side'] for x in counter),signal_fill_difference_cents=dist([x['signal_fill_difference_cents'] for x in rr]),prebook_fill_difference_cents=dist([x['pre_book_fill_difference_cents'] for x in rr]),finalbook_fill_difference_cents=dist([x['final_book_fill_difference_cents'] for x in rr]),latency={k:dist([x['latency'][k] for x in rr]) for k in rr[0]['latency']} if rr else {})
save('receipt_results.json',out);save('receipt_rows.json',rows)
print(json.dumps(out,indent=2))
