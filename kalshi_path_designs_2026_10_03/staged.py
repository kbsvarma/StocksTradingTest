"""Exploratory small starter, one confirmed increase, maximum five per market."""
from impulse import *
import designs
designs.gate=eligible
C=next(c for c in configurations() if c['id']==801)

def event_inputs(cache,qty,markets,books,source):
    data=cache[source,.85,qty];rec,a,fs=data;lookup={(m['ticker'],f['t']):f for m in markets for f in m['features']}
    for oid in fs:fs[oid]=lookup[rec[oid]['ticker'],rec[oid]['t']]
    a=select(data,C);out=[]
    for ar in a:
        e=dict(rec[int(ar[s.OID])],lane=int(ar[s.LANE]));e['fills']={}
        for n in ((qty,) if e['lane']==2 else (5,5-qty)):
            if source=='quote':fill=(e['entry'],s.fee(e['entry'],n))
            else:
                bb=books[e['ticker']];j=bisect.bisect_left([b[0] for b in bb],e['entered']);book=bb[j]
                floor=.85 if e['lane'] in (0,2) else .93
                fill=s.book_fill(book,e['side'],e['cap'],floor,n)
            assert fill is not None;e['fills'][n]=fill
        out.append(e)
    return out

def simulate(ev,qty=1,stress=.01,capcost=False,baseline=False,miss=0,seed=0):
    seen={};active=[];out=[];cash=50.;daily=collections.defaultdict(float);own=collections.defaultdict(float)
    rejected=collections.Counter()
    for e in ev:
        if baseline and e['lane']==2:continue
        left=[]
        for p in active:
            if p['settled']<=e['entered']:
                cash+=p['quantity']*p['won'];day=s.etday(p['settled']);daily[day]+=p['net']
                if p['lane']==1:own[day]+=p['net']
            else:left.append(p)
        active=left;prior=seen.get(e['ticker'],[]);upgrade=False
        if prior:
            if baseline or len(prior)>1 or prior[0]['lane']!=2 or e['lane']==2 or prior[0]['side']!=e['side']:continue
            if not any(p['ticker']==e['ticker'] for p in active):continue
            upgrade=True;n=5-qty
        else:n=qty if e['lane']==2 else 5
        if any(p['asset']==e['asset'] and p['ticker']!=e['ticker'] for p in active):continue
        count=sum(p['quantity'] for p in active);risk=sum(p['reserve'] for p in active);owncount=sum(p['quantity'] for p in active if p['lane']==1)
        reserve=n*(1. if e['lane']==1 else .95);day=s.etday(e['entered'])
        if count+n>20 or risk+reserve>19+1e-8 or daily[day]-risk-reserve < -10-1e-8:
            rejected['risk']+=1;continue
        if e['lane']==1 and (owncount+n>20 or own[day]-owncount-n < -25-1e-8):continue
        if cash<max(5.,reserve)-1e-8:continue
        if miss:
            # Stable market/lane-specific coin independent of outcomes. Failed IOC
            # closes that logical entry opportunity, including upgrades; no retry.
            key=e['ticker']+str(e['lane'])+str(seed)
            value=int(hashlib.sha256(key.encode()).hexdigest()[:12],16)/(16**12)
            if value<miss:
                if prior:prior.append(dict(e,quantity=0,failed=True))
                else:seen[e['ticker']]=[dict(e,quantity=0,failed=True)]
                continue
        if prior and prior[0].get('failed'):continue
        price,paid=e['fills'][n]
        if capcost:price=e['cap'];paid=s.fee(price,n)
        debit=n*price+paid+n*stress;cash-=debit
        r={k:v for k,v in e.items() if k!='fills'}
        r.update(quantity=n,entry=price,fee=paid,reserve=reserve,pnl=n*(e['won']-price)-paid,pnl_cap=n*(e['won']-e['cap'])-s.fee(e['cap'],n),net=n*e['won']-debit,upgrade=upgrade)
        seen.setdefault(e['ticker'],[]).append(r);active.append(r);out.append(r)
        assert sum(x['quantity'] for x in seen[e['ticker']])<=5
    return out

def metrics(rr,stress=.01):
    m=s.old.metrics(rr,stress);by={r['ticker']:r['won'] for r in rr}
    m.update(markets=len(by),market_wins=sum(by.values()),market_losses=len(by)-sum(by.values()),contracts=sum(r['quantity'] for r in rr),starters=sum(r['lane']==2 for r in rr),upgrades=sum(r['upgrade'] for r in rr))
    return m

def main():
    markets=impulse_features(pickle.loads((P/'features.pickle').read_bytes()));books=s.old.load_books();results={};trades={};parity=[]
    for qty in [1,2]:
        cache=pickle.loads((P/f'inputs_size{qty}.pickle').read_bytes());results[str(qty)]={};trades[str(qty)]={}
        for source in ['quote','book','book10']:
            ev=event_inputs(cache,qty,markets,books,source);results[str(qty)][source]={};trades[str(qty)][source]={}
            for co in (('all',)+s.PERIODS if source=='quote' else ('all',)+s.PERIODS[:2]):
                ee=ev if co=='all' else [e for e in ev if s.BOUNDS[s.PERIODS.index(co)]<=e['close']<s.BOUNDS[s.PERIODS.index(co)+1]];mm={}
                for label,stress,capcost in [('primary',.01,False),('two_cent',.02,False),('limit_cost',.01,True)]:
                    b=simulate(ee,qty,stress,capcost,True);j=simulate(ee,qty,stress,capcost);expected=run(cache[source,.85,qty],period=co,stress=stress,capcost=capcost)
                    assert sorted(r['ticker'] for r in b)==sorted(r['ticker'] for r in expected)
                    assert metrics(b,stress)['net']==s.old.metrics(expected,stress)['net'];parity.append([qty,source,co,label,len(b)])
                    m=metrics(j,stress);base=metrics(b,stress);m.update(baseline=base,delta_net=round(m['net']-base['net'],5),extra_markets=m['markets']-base['markets']);mm[label]=m
                    if label=='primary':trades[str(qty)][source][co]=j
                results[str(qty)][source][co]=mm
                print('STAGED',qty,source,co,{k:mm['primary'][k] for k in ['markets','n','net','delta_net','extra_markets','drawdown','upgrades']},'2c',mm['two_cent']['delta_net'],flush=True)
    save('staged_results.json',results);save('staged_validation.json',dict(baseline_comparisons=parity));(P/'staged_trades.pickle').write_bytes(pickle.dumps(trades,protocol=5))

if __name__=='__main__':main()
