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

def simulate(rows,budget=10,exposure=19,stress=.01,cash0=50,cash2=50):
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
        elif 5*(len(active)+1)>20:reason='contracts'
        elif projected>exposure+1e-8:reason='exposure'
        elif daily[day]-projected < -budget-1e-8:reason='daily_including_open_risk'
        elif x['lane']==s.r.Z3:
            own=[y for y in active if y['lane']==s.r.Z3];ownrisk=risk+sum(y['reserve'] for y in own)
            if 5*(len(own)+1)>20 or ownrisk>20+1e-8:reason='z3_own_exposure'
            elif lane_daily[(day,s.r.Z3)]-ownrisk < -25-1e-8:reason='z3_daily'
        if not reason and cash[route(x['asset'])]<max(5,risk)-1e-8:reason='route_cash'
        if reason:blocks[reason]+=1;continue
        net=x['pnl']-5*stress
        cash[route(x['asset'])]-=5*x['entry']+x['fee']+5*stress
        active.append(dict(x,net=net,reserve=risk));seen.add(x['ticker'])
    settle(float('inf'))
    return sorted(closed,key=lambda r:(r['settled'],r['ticker'])),dict(blocks)

def summary(rows):
    m=s.compact(rows);return {k:m[k] for k in ('trades','wins','losses','net','settled_drawdown')}

def main():
    other=pickle.loads((s.P/'features.pickle').read_bytes());crypto=pickle.loads((P.parent/'kalshi_btc_eth_quant_2026_10_02/features.pickle').read_bytes())
    choices={a:s.CONFIG[i] for a,i in RULES.items()}
    results={};opportunities={}
    for scenario,mode,stress in [('fees1c','opening',.01),('fees2c','opening',.02),('cap1c','cap_cost',.01)]:
        cp,_=s.r.opportunities([dict(m,multiplier=1) for m in crypto if s.r.START<=m['close']<s.r.SPLIT],mode)
        allnew=s.pool([m for m in other if m['asset'] in RULES],mode)
        rows=cp+[x for a,c in choices.items() for x in s.select([r for r in allnew if r['asset']==a],c)]
        opportunities[scenario]=rows
        if scenario=='fees1c':
            wti,_=simulate([x for x in rows if x['asset']=='WTI']);assert summary(wti)==dict(trades=362,wins=345,losses=17,net=38.7212,settled_drawdown=9.8372)
            cr,_=simulate(cp);assert len(cr)==334 and abs(sum(x['net'] for x in cr)-69.5823)<1e-7
        for period,start,end,onlynew in [('matched_all8_Sep23_Oct2',s.r.ts('2026-09-23T00:00:00Z'),s.r.SPLIT,False),('matched_available_Sep11_Oct2',s.r.START,s.r.SPLIT,False),('noncrypto_only_Oct3_7',s.r.SPLIT,s.r.END,True)]:
            subset=[x for x in rows if start<=x['close']<end and (not onlynew or x['asset'] in RULES)]
            key=period+'/'+scenario;results[key]={}
            for name,budget,exposure in [('current',10,19),('daily15',15,19),('daily15_exposure20',15,20),('daily20_exposure20',20,20)]:
                for add in (False,True):
                    ss=[x for x in subset if add or x['asset']!='WTI'];trades,blocked=simulate(ss,budget,exposure,stress)
                    results[key][name+('/with_WTI' if add else '/without_WTI')]=dict(**summary(trades),blocked=blocked,by_asset={a:summary([x for x in trades if x['asset']==a]) for a in ASSETS if any(x['asset']==a for x in trades)})
            print(key,{n:{k:m[k] for k in ('trades','net','settled_drawdown')} for n,m in results[key].items()},flush=True)
    (P/'budget_results.json').write_text(json.dumps(dict(results=results,rules=RULES,cash_per_route=50,contracts_per_entry=5,max_contracts=20,
         note='Reused selected history, quote proxies without five-contract historical depth. Full crypto feature coverage ends Oct2; later comparison excludes crypto. No funding or live changes.'),indent=2)+'\n')
    with gzip.open(P/'budget_opportunities.json.gz','wt') as f:json.dump(opportunities,f)

if __name__=='__main__':main()
