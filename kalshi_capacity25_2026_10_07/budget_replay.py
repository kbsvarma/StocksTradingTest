"""Offline eight-market budget comparison; no account or order transport."""
from collections import Counter,defaultdict
import json,gzip,pickle,sys
from pathlib import Path
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P.parent/'kalshi_noncrypto_tuning_2026_10_07'))
import study as s
ASSETS=('BTC','ETH','SOL','XRP','COPPER','NATGAS','EURUSD','WTI')
RULES={'COPPER':1239,'NATGAS':0,'EURUSD':1183,'WTI':1238}
def route(a):return 0 if a=='EURUSD' else 2

def simulate(rows,budget=10,exposure=19,stress=.01,cash0=50,cash2=50,max_contracts=20,z3_contracts=20,z3_exposure=20,z3_budget=25):
    cash={0:cash0,2:cash2};active=[];closed=[];seen=set();blocks=Counter();daily=defaultdict(float);lane_daily=defaultdict(float)
    def settle(t):
        nonlocal active
        rest=[]
        for x in sorted(active,key=lambda r:(r['settled'],r['ticker'])):
            if x['settled']<=t:
                cash[route(x['asset'])]+=5*x['won'];daily[s.r.etday(x['settled'])]+=x['net'];lane_daily[(s.r.etday(x['settled']),x['lane'])]+=x['net'];closed.append(x)
            else:rest.append(x)
        active=rest
    for x in sorted(rows,key=lambda r:(r['entered'],r['lane']!=s.r.Z2,ASSETS.index(r['asset']))):
        settle(x['entered'])
        if x['ticker'] in seen:continue
        risk=5*float(s.r.LANES[x['lane']].reserve);projected=risk+sum(y['reserve'] for y in active);day=s.r.etday(x['entered'])
        reason=''
        if any(y['asset']==x['asset'] for y in active):reason='asset'
        elif 5*(len(active)+1)>max_contracts:reason='contracts'
        elif projected>exposure+1e-8:reason='exposure'
        elif daily[day]-projected < -budget-1e-8:reason='daily_including_open_risk'
        elif x['lane']==s.r.Z3:
            own=[y for y in active if y['lane']==s.r.Z3];ownrisk=risk+sum(y['reserve'] for y in own)
            if 5*(len(own)+1)>z3_contracts or ownrisk>z3_exposure+1e-8:reason='z3_own_exposure'
            elif lane_daily[(day,s.r.Z3)]-ownrisk < -z3_budget-1e-8:reason='z3_daily'
        if not reason and cash[route(x['asset'])]<max(5,risk)-1e-8:reason='route_cash'
        if reason:blocks[reason]+=1;continue
        net=x['pnl']-5*stress
        cash[route(x['asset'])]-=5*x['entry']+x['fee']+5*stress
        active.append(dict(x,net=net,reserve=risk));seen.add(x['ticker'])
    settle(float('inf'))
    return sorted(closed,key=lambda r:(r['settled'],r['ticker'])),dict(blocks)

def summary(rows):
    m=s.compact(rows);return {k:m[k] for k in ('trades','wins','losses','net','settled_drawdown')}


SCENARIOS={
 'current_20_19_15':dict(max_contracts=20,exposure=19,budget=15,z3_contracts=20,z3_exposure=20),
 '25_25_20':dict(max_contracts=25,exposure=25,budget=20,z3_contracts=25,z3_exposure=25),
 '25_25_25':dict(max_contracts=25,exposure=25,budget=25,z3_contracts=25,z3_exposure=25),
 '25_25_25_old_premium_cap':dict(max_contracts=25,exposure=25,budget=25,z3_contracts=20,z3_exposure=20),
}
def concurrency(rows):
 active=[];peak=0;reserve=0;fifth=0;byday=defaultdict(float)
 for x in sorted(rows,key=lambda x:x['entered']):
  active=[y for y in active if y['settled']>x['entered']]
  active.append(x);peak=max(peak,len(active));reserve=max(reserve,sum(y['reserve'] for y in active))
  fifth+=len(active)==5;byday[s.r.etday(x['settled'])]+=x['net']
 return dict(peak_positions=peak,peak_reserved=round(reserve,4),fifth_position_entries=fifth,worst_settled_day=round(min(byday.values(),default=0),4))
def main():
 with gzip.open(P.parent/'kalshi_wti_2026_10_07/budget_opportunities.json.gz','rt') as f:pools=json.load(f)
 results={}
 for cost,stress in [('fees1c',.01),('fees2c',.02),('cap1c',.01)]:
  for period,start,end,onlynew in [('matched_all8_Sep23_Oct2',s.r.ts('2026-09-23T00:00:00Z'),s.r.SPLIT,False),('matched_available_Sep11_Oct2',s.r.START,s.r.SPLIT,False),('noncrypto_only_Oct3_7',s.r.SPLIT,s.r.END,True)]:
   rows=[x for x in pools[cost] if start<=x['close']<end and (not onlynew or x['asset'] in RULES)]
   results[period+'/'+cost]={}
   for name,params in SCENARIOS.items():
    fills,blocks=simulate(rows,stress=stress,**params)
    results[period+'/'+cost][name]=dict(**summary(fills),**concurrency(fills),blocked=blocks)
   print(period+'/'+cost,{k:{a:v[a] for a in ('trades','net','settled_drawdown','peak_positions','fifth_position_entries')} for k,v in results[period+'/'+cost].items()},flush=True)
 assert results['matched_all8_Sep23_Oct2/fees1c']['current_20_19_15']['net']==52.0501
 (P/'results.json').write_text(json.dumps(dict(results=results,scenarios=SCENARIOS,cash_per_route=50,contracts_per_entry=5,note='Same frozen rules and cached research inputs. Reused selected history, quote proxies, not new holdout or fill-depth evidence.'),indent=2)+'\n')
if __name__=='__main__':main()
