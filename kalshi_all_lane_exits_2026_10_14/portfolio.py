"""Research-only event-driven cash/risk replay of previously screened designs."""
from entry_grid import *
import heapq
from zoneinfo import ZoneInfo
ET=ZoneInfo('America/New_York'); ACTIVE=[a for a in ASSETS if a not in ('COPPER','USDCAD')]
def day(t):return datetime.datetime.fromtimestamp(t,ET).date().isoformat()
def simulate(rows,exits,begin,end,extra=0):
 cash={0:50.,2:50.};heap=[];daily=collections.defaultdict(float);premiumdaily=collections.defaultdict(float);accepted=[];reject=collections.Counter();serial=0;maxrisk=maxcontracts=0
 def release(t):
  while heap and heap[0][0]<=t:
   _,_,route,cost,net,rec=heapq.heappop(heap);cash[route]+=cost+net;daily[day(rec['realized'])]+=net
   if rec['lane']==Z3:premiumdaily[day(rec['realized'])]+=net
 for r,x in zip(rows,exits):
  if not begin<=r['entered']<end:continue
  release(r['entered']);route=0 if r['asset'] in ('EURUSD','USDCAD') else 2
  cost=r['cost']+r['entry_fee']+r.get('entry_friction',0)+5*extra
  reserve=r['reserve'];openrisk=sum(h[5]['reserve'] for h in heap);contracts=5*len(heap)
  if any(h[5]['asset']==r['asset'] for h in heap):reject['asset_exposure']+=1;continue
  if cash[route]+1e-8<max(cost,reserve,5.):reject['route_cash']+=1;continue
  if contracts+5>25:reject['contracts']+=1;continue
  if openrisk+reserve>25+1e-8:reject['exposure']+=1;continue
  if openrisk+reserve-daily[day(r['entered'])]>20+1e-8:reject['daily_including_open']+=1;continue
  ownrisk=sum(h[5]['reserve'] for h in heap if h[5]['lane']==Z3)+reserve
  if r['lane']==Z3 and ownrisk-premiumdaily[day(r['entered'])]>25+1e-8:reject['premium_daily']+=1;continue
  net=x['net']-5*extra*(1+int(x['exit']));out=dict(x,entered=r['entered'],entry_price=r['cost']/5,entry_gate=r['entry_gate'],entry_z=r['entry_z'],reserve=reserve)
  out['net']=net;out['delta']=net-r['hold_net'];cash[route]-=cost;serial+=1
  heapq.heappush(heap,(out['realized'],serial,route,cost,net,out));accepted.append(out)
  maxrisk=max(maxrisk,openrisk+reserve);maxcontracts=max(maxcontracts,contracts+5)
  assert min(cash.values())>=-1e-7 and maxrisk<=25+1e-7 and maxcontracts<=25
 release(float('inf'));summary=metrics(accepted);summary.update(blocked=dict(reject),candidates=sum(begin<=r['entered']<end for r in rows),days=(end-begin)/86400,trades_per_day=len(accepted)/((end-begin)/86400),cash=cash,max_open_principal=maxrisk,max_contracts=maxcontracts)
 assert abs(sum(cash.values())-100-summary['net'])<1e-6
 return summary,accepted
def main():
 data=pickle.load(gzip.open(P/'inputs.pkl.gz'));sel=json.loads((P/'entry_selections.json').read_text());cc={c['id']:c for c in rules()};ec={c['id']:c for c in entry_configs()};pools={a:candidates([m for m in data['markets'].values() if m['asset']==a]) for a in ASSETS};cache={}
 def prepare(assets,designs,policy):
  rows=[];xx=[]
  for a in assets:
   design=designs.get(a,'current');rs=pick(pools[a],ec[design]);exitid=sel[a][design]['exit_id'] if policy=='fit_selected' else policy
   for r in rs:
    if exitid=='hold':x=dict(ticker=r['ticker'],asset=a,lane=r['lane'],won=r['won'],quantity=5,hold=r['hold_net'],net=r['hold_net'],delta=0,exit=False,trigger=None,exit_fee=0,friction=0,price=None,source_missing=0,quote_missing=False,realized=r['settled'])
    else:
     key=(r['ticker'],r['t'],r['lane'],r['entry_gate'],exitid)
     if key not in cache:cache[key]=exit_result(r,cc[exitid],path(r,data))
     x=cache[key]
    rows.append(r);xx.append(x)
  order=sorted(range(len(rows)),key=lambda i:(rows[i]['entered'],rows[i]['ticker']));return [rows[i] for i in order],[xx[i] for i in order]
 best={}
 for a in ASSETS:
  ranking=sorted([(k,v) for k,v in sel[a].items() if v['fit_trades']>=30],key=lambda kv:-kv[1]['fit_net']);best[a]=ranking[0][0] if ranking else 'current'
 begin=min(m['start'] for m in data['markets'].values());end=max(m['close'] for m in data['markets'].values())+60
 scenarios=[]
 for universe,assets in [('active7',ACTIVE),('all9',list(ASSETS))]:
  for dname,ds in [('current',{}),('fit_best',best)]+[(f'paired_z{z:g}_t1_10',{a:f'paired_z{z:g}_t1_10' for a in assets}) for z in [1.25,1.5,1.8,2,2.5,3]]:
   for policy in ['hold','fit_selected','drop_2_g120_p60']:
    scenarios.append((f'{universe}/{dname}/{policy}',assets,ds,policy))
 for z in [1.25,1.5,1.75,1.8]:
  for lo,hi in [(1,10),(3,10)]:
   ds={'XRP':f'paired_z{z:g}_t{lo}_{hi}'}
   for policy in ['hold','drop_2_g120_p60']:
    scenarios.append((f'active7/XRP_only_z{z:g}_t{lo}_{hi}/{policy}',ACTIVE,ds,policy))
 results=[];ledgers={}
 for name,assets,ds,policy in scenarios:
  rows,xx=prepare(assets,ds,policy);record=dict(name=name,designs={a:ds.get(a,'current') for a in assets},policy=policy,periods={})
  for period,lo,hi in [('full',begin,end),('fit',LAUNCH,SPLIT),('later',SPLIT,end),('older',begin,LAUNCH)]:
   summary,ledger=simulate(rows,xx,lo,hi);record['periods'][period]=summary
   if period=='later':ledgers[name]=ledger
  record['later_friction_2c']=simulate(rows,xx,SPLIT,end,.01)[0];record['later_friction_5c']=simulate(rows,xx,SPLIT,end,.04)[0];results.append(record)
  print(name,'later',record['periods']['later']['trades'],round(record['periods']['later']['net'],4),flush=True)
 save('portfolio_results.json',results);save('portfolio_fit_choices.json',best)
 with gzip.open(P/'portfolio_later_ledgers.json.gz','wt') as f:json.dump(ledgers,f)
if __name__=='__main__':main()
