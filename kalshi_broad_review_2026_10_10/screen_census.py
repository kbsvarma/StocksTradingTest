import json,collections,datetime,math,re
from pathlib import Path
from decimal import Decimal,ROUND_CEILING
P=Path(__file__).resolve().parent;D=P/'data';O=P/'results';O.mkdir(exist_ok=True)
series={x['ticker']:x for x in json.loads((D/'series.json').read_text())['series']};events={e['event_ticker']:e for e in map(json.loads,(D/'events.jsonl').read_text().splitlines())};now=datetime.datetime.fromisoformat(json.loads((D/'census_receipt.json').read_text())['finished_utc']).timestamp()
def num(m,k,default=0):
 try:return float(m.get(k) or default)
 except:return default
def quote(m,side):
 b=num(m,'yes_bid_dollars');a=num(m,'yes_ask_dollars',1)
 if not 0<=b<=a<=1:return None
 p,n=(a,num(m,'yes_ask_size_fp')) if side=='yes' else (1-b,num(m,'yes_bid_size_fp'))
 if not 0<p<1 or n<5:return None
 return p,n
def fee(p,mult):return math.ceil((.07*mult*5*p*(1-p)-1e-12)*10000)/10000
by_series=collections.defaultdict(list);opps=[];coverage=collections.Counter();mk={}
for e in events.values():
 ms=[m for m in e.get('markets',[]) if m.get('status') in ['active','open'] and not m.get('mve_collection_ticker') and datetime.datetime.fromisoformat(m['close_time'].replace('Z','+00:00')).timestamp()>now]
 t=e['series_ticker'];s=series.get(t,{})
 for m in ms:by_series[t].append(m);mk[m['ticker']]=m
 mult=e.get('fee_multiplier_override') if e.get('fee_multiplier_override') is not None else s.get('fee_multiplier');ft=e.get('fee_type_override') or s.get('fee_type')
 if mult is None or ft not in ['quadratic','quadratic_with_maker_fees']:coverage['unsupported_fee_events']+=1;continue
 mult=float(mult)
 def check(legs,payout,kind):
  coverage[kind+'_examined']+=1
  if not all(q for _,_,q in legs):return
  coverage[kind+'_priced']+=1
  cost=sum(5*q[0]+fee(q[0],mult) for _,_,q in legs)
  # No arbitrary capital cap in discovery; report gross cash required.
  profit=5*payout-cost;stress=profit-.05*len(legs)
  if profit>0:opps.append({'kind':kind,'event':e['event_ticker'],'series':t,'title':e.get('title'),'cost':cost,'profit':profit,'stress_1c_profit':stress,'legs':[{'ticker':m['ticker'],'side':side,'ask':q[0],'depth':q[1],'rules':m.get('rules_primary'),'close_time':m['close_time']} for m,side,q in legs]})
 if e.get('mutually_exclusive') and len(ms)>=2:
  # Any subset of mutually exclusive outcomes: at most one NO leg loses. Same currency assumed $1 contracts only.
  eligible=[m for m in ms if num(m,'notional_value_dollars',1)==1]
  if len(eligible)>=2:check([(m,'no',quote(m,'no')) for m in eligible],len(eligible)-1,'exclusive_no')
  priced=sorted([m for m in eligible if quote(m,'no')],key=lambda m:quote(m,'no')[0])
  for k in range(2,min(20,len(priced))+1):check([(m,'no',quote(m,'no')) for m in priced[:k]],k-1,'exclusive_no_subset')
 groups=collections.defaultdict(list)
 for m in ms:
  if m.get('strike_type') in ['greater','greater_or_equal'] and m.get('floor_strike') is not None:
   key=(m['strike_type'],json.dumps(m.get('custom_strike'),sort_keys=True),m['close_time'],m.get('notional_value_dollars'))
   groups[key].append(m)
 for g in groups.values():
  g.sort(key=lambda m:float(m['floor_strike']))
  # For each upper threshold, cheapest eligible lower YES dominates every
  # other lower leg under this series' monotone fee-inclusive entry cost.
  levels=collections.defaultdict(list)
  for item in g:levels[float(item['floor_strike'])].append(item)
  best=None
  for strike,same in sorted(levels.items()):
   for higher in same:
    if best is not None:check([(best,'yes',quote(best,'yes')),(higher,'no',quote(higher,'no'))],1,'threshold')
   for lower in same:
    v=quote(lower,'yes')
    if v and (best is None or v[0]<quote(best,'yes')[0]):best=lower
rows=[];cats=collections.defaultdict(lambda:collections.Counter())
for t,s in series.items():
 ms=by_series.get(t,[]);ev={m['event_ticker'] for m in ms};valid=[];tight=[];short=[]
 for m in ms:
  q=quote(m,'yes');qn=quote(m,'no');a=num(m,'yes_ask_dollars',1);b=num(m,'yes_bid_dollars')
  if q and qn:valid.append((a-b)*100)
  if q and qn and a-b<=.02000001:tight.append(m)
  if q and qn and a-b<=.02000001 and datetime.datetime.fromisoformat(m['close_time'].replace('Z','+00:00')).timestamp()-now<=7*86400:short.append(m)
 cat=s.get('category','Unknown');c=cats[cat];c['catalog_series']+=1;c['open_series']+=bool(ms);c['events']+=len(ev);c['markets']+=len(ms);c['tight_five_contract_markets']+=len(tight);c['short_tight_markets']+=len(short);c['reported_open_24h_contracts']+=sum(num(m,'volume_24h_fp') for m in ms)
 reason='No current standard open market in snapshot; check recent settlements for recurring weekend families' if not ms else ('Executable-quote candidate, no demonstrated edge' if short else ('Longer horizon / no short tight quote; model unverified' if tight else 'No two-sided five-contract quote at <=2-cent spread in snapshot'))
 rows.append({'ticker':t,'title':s['title'],'category':cat,'frequency':s.get('frequency'),'open_events':len(ev),'open_markets':len(ms),'reported_24h_contracts':sum(num(m,'volume_24h_fp') for m in ms),'tight_five_contract_markets':len(tight),'within_7d_tight_markets':len(short),'assessment':reason,'sources':s.get('settlement_sources'),'fee_type':s.get('fee_type'),'fee_multiplier':s.get('fee_multiplier')})
(O/'series_assessments.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows));(O/'category_screen.json').write_text(json.dumps(dict(cats),indent=2));(O/'structural_screen.json').write_text(json.dumps({'coverage':dict(coverage),'opportunities':sorted(opps,key=lambda x:-x['stress_1c_profit']),'markets':len(mk),'events_returned':len(events),'snapshot_is_not_atomic':True},indent=2))
print('market statuses',collections.Counter(m.get('status') for e in events.values() for m in e.get('markets',[])));print('open',len(mk),'series',len(rows),'opportunities',len(opps),'stressed',sum(x['stress_1c_profit']>0 for x in opps));print(json.dumps(dict(cats),indent=2));print('top short series',[(r['ticker'],r['within_7d_tight_markets'],round(r['reported_24h_contracts'])) for r in sorted(rows,key=lambda r:r['reported_24h_contracts'],reverse=True) if r['within_7d_tight_markets']][:55]);print('opps',json.dumps(sorted(opps,key=lambda x:-x['stress_1c_profit'])[:3]))
