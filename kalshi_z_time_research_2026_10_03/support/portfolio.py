"""Research-only portfolio replay with causal risk and settlement release."""
import collections,datetime as dt,json,pickle
from zoneinfo import ZoneInfo
import numpy as np
from research import P,RULES,load_books,replay,metrics,fee,ts

NAMES=['current_signal95','premium_z3','gaussian_edge8']
ET=ZoneInfo('America/New_York')
def etday(t):return dt.datetime.fromtimestamp(t,ET).date().isoformat()

def portfolio(rows,names,max_contracts=4,max_risk=4,daily_budget=5,stress=.01,cap_cost=False,start_cash=50):
    opportunities=[]
    for name in names:
        opportunities.extend(dict(r,lane=name) for r in rows.get(name,[]))
    opportunities.sort(key=lambda r:(r['entered'],names.index(r['lane']),r['asset']))
    active=[];closed=[];entered=set();cash=float(start_cash);realized=collections.defaultdict(float);blocked=collections.Counter()
    def settle(t):
        nonlocal cash,active
        left=[]
        for r in active:
            if r['settled']<=t:
                cash+=r['quantity']*r['won'];realized[etday(r['settled'])]+=r['pnl']-stress*r['quantity'];closed.append(r)
            else:left.append(r)
        active=left
    for r in opportunities:
        settle(r['entered'])
        if r['ticker'] in entered:continue
        if any(p['asset']==r['asset'] for p in active):blocked['asset']+=1;continue
        n=r['quantity'];reserve=.95 if r['lane']=='current_signal95' else 1.
        risk=sum(p['reserve'] for p in active)+reserve*n
        if sum(p['quantity'] for p in active)+n>max_contracts:blocked['contracts']+=1;continue
        if risk>max_risk+1e-8:blocked['exposure']+=1;continue
        if realized[etday(r['entered'])]-risk < -daily_budget-1e-8:blocked['daily']+=1;continue
        if cap_cost:r=dict(r,entry=r['cap'],fee=fee(r['cap'],n),pnl=r['pnl_cap'])
        debit=n*r['entry']+r['fee']+stress*n
        if cash<max(n,debit):blocked['cash']+=1;continue
        cash-=debit;active.append(dict(r,reserve=reserve*n));entered.add(r['ticker'])
    settle(float('inf'))
    return sorted(closed,key=lambda r:(r['close'],r['asset'])),dict(blocked)

def boot(rows,days,draws=20000):
    sums=np.array([sum(r['pnl']-.01*r['quantity'] for r in rows if r['day']==d) for d in days]);counts=np.array([sum(r['quantity'] for r in rows if r['day']==d) for d in days])
    if not counts.sum():return None
    ix=np.random.default_rng(20261002).integers(0,len(days),size=(draws,len(days)))
    den=counts[ix].sum(axis=1);val=sums[ix].sum(axis=1)[den>0]/den[den>0]
    return {'lower95_ev_c':round(float(np.quantile(val,.025))*100,4),'upper95_ev_c':round(float(np.quantile(val,.975))*100,4),
            'lower_bonferroni12_ev_c':round(float(np.quantile(val,.05/12))*100,4)}

def detailed(rows,days):
    s=metrics(rows);s.update(fee_only=metrics(rows,0),stress2c=metrics(rows,.02),whole_cent_net_after1c=round(sum(r['quantity']*(r['won']-r['entry']-.01)-fee(r['entry'],r['quantity'],.01) for r in rows),5),bootstrap=boot(rows,days),
      asset={a:metrics([r for r in rows if r['asset']==a]) for a in ('BTC','ETH','SOL','XRP')},daily={d:round(sum(r['pnl']-.01*r['quantity'] for r in rows if r['day']==d),5) for d in days})
    best=sorted(s['daily'].values(),reverse=True)
    s['remove_best3_days']=round(s['net']-sum(best[:3]),5)
    s['two_extra_winners_flip_to_loss']=round(s['net']-2*(rows[0]['quantity'] if rows else 1),5)
    return s

def main():
    markets=pickle.loads((P/'features.pickle').read_bytes());model=json.loads((P/'model.json').read_text())['coefficients']
    selected=[r for r in RULES if r.name in NAMES];books=load_books();result={};saved={}
    # Cut full periods at midnight UTC: don't give a short partial day equal statistical weight.
    markets=[m for m in markets if ts('2026-08-13T00:00:00Z')<=m['close']<ts('2026-10-02T00:00:00Z')]
    for scenario,bb,delay in [('quote',None,0),('book',books,0),('quote_delay60',None,60),('book_delay10',books,10)]:
        signals,_=replay(markets,model,bb,all_signals=True,rules=selected,delay=delay)
        scen={};saved[scenario]={}
        for co in ('fit','selection','later'):
            rows={k:[r for r in v if r['cohort']==co] for k,v in signals.items()}
            if bb is not None and co=='later':continue
            days=sorted({dt.datetime.fromtimestamp(m['close'],dt.timezone.utc).date().isoformat() for m in markets if ('fit' if m['close']<ts('2026-08-28T00:00:00Z') else 'selection' if m['close']<ts('2026-09-12T00:00:00Z') else 'later')==co})
            out={};tr={}
            for label,names in [('current_one',['current_signal95']),('premium',['premium_z3']),('value',['gaussian_edge8']),('parallel_pair',['premium_z3','gaussian_edge8']),('joint_one',['current_signal95','premium_z3','gaussian_edge8'])]:
                rr,blocked=portfolio(rows,names);out[label]=dict(detailed(rr,days),blocked=blocked);tr[label]=rr
                cap,_=portfolio(rows,names,cap_cost=True);out[label]['cap_replay_net']=metrics(cap)['net']
            base={r['ticker'] for r in tr['current_one']};pair={r['ticker'] for r in tr['parallel_pair']};joint={r['ticker'] for r in tr['joint_one']}
            out['overlap']={'baseline_markets':len(base),'parallel_markets':len(pair),'parallel_markets_not_in_baseline':len(pair-base),'intersection':len(pair&base),'joint_extra_vs_baseline':len(joint-base),'joint_displaced_baseline':len(base-joint)}
            saved[scenario][co]=tr;scen[co]=out
            print(scenario,co,{k:(v['n'],v['net']) for k,v in out.items() if 'n' in v},'overlap',out['overlap'],flush=True)
        result[scenario]=scen
    # Isolate current 5-contract risk limits, same strategy and quote inputs, later window.
    current5,_=replay(markets,model,None,quantity=5,all_signals=True,rules=selected[:1])
    current5={k:[r for r in v if r['cohort']=='later'] for k,v in current5.items()};limits={}
    for label,n,risk,budget in [('current8',8,7.60,10),('twenty_keepdaily10',20,19,10),('twenty_daily25',20,19,25)]:
        rr,blocked=portfolio(current5,['current_signal95'],n,risk,budget)
        limits[label]=dict(metrics(rr),blocked=blocked)
    result['current5_limits_quote_only']=limits
    (P/'portfolio_results.json').write_text(json.dumps(result,indent=2));(P/'portfolio_trades.json').write_text(json.dumps(saved));print('5-contract limits',limits,flush=True)

if __name__=='__main__':main()
