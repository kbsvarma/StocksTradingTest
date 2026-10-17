"""Execution and risk audit of fixed-model high-participation secondary leads.

These are NOT the original highest-selection-net routing winner. The primary
online winner failed. No thresholds changed; this is a disclosed secondary
look within the prespecified family, requiring untouched future validation.
"""
import collections, gzip, json, math, pickle
import numpy as np
import screen as s
import adaptive as ad
import validate_candidates as vc
P=s.P

def run(pair,route,mode,bb,quantity=1,edge=.01):
    misses=collections.Counter()
    for rem in range(14,0,-1):
        offers=[]
        for a in ('BTC','ETH'):
            m=pair.get(a);f=m['new_features'].get(rem) if m else None
            if not f:continue
            ch=ad.choice(f,'rich')
            if ch:offers.append((m,f,ch))
        if route=='best':offers.sort(key=lambda x:x[2][1],reverse=True)
        ready=[o for o in offers if o[2][1]>=edge]
        fallback=not ready and rem==1
        if fallback:ready=offers
        for m,f,ch in ready:
            if mode.startswith('book'):
                import bisect
                rows=bb.get(m['ticker'],[]);tt=[b[0] for b in rows]
                ix=bisect.bisect_left(tt,f['t']+(10 if mode=='book_delay10' else .3))
                if ix==len(rows) or tt[ix]>f['t']+35:misses['no_timely_book']+=1;continue
                got=vc.fill(rows[ix],ch[0],quantity)
                if got is None:misses['insufficient_depth']+=1;continue
                price,paid=got
                tr=dict(ticker=m['ticker'],asset=m['asset'],close=m['close'],day=s.day(m['close']),cohort=m['cohort'],
                    decision=f['t'],entered=tt[ix],side=ch[0],entry=price,exit=int(m['result']==ch[0]),exit_ts=max(m['close']+1,m['settled']),
                    exit_reason='settlement',quantity=quantity,fee=paid,pnl=quantity*(int(m['result']==ch[0])-price)-paid,
                    settlement_win=int(m['result']==ch[0]),legs=1)
            else:
                tr=s.trade(m,f,ch[0],delay=mode=='delay60',quantity=quantity)
                if tr is None:misses['non_executable_quote']+=1;continue
                if mode=='worse_signal_open':
                    price=max(tr['entry'],f['a'] if ch[0]==1 else 1-f['b'])
                    tr.update(entry=price,fee=s.fee(price,quantity),pnl=quantity*(tr['exit']-price)-s.fee(price,quantity))
            tr.update(rem=rem,modeled_edge=ch[1],forced_fallback=fallback);return tr,misses
    return None,misses

def uncertainty(rows):
    result=vc.uncertainty(rows)
    daily=collections.defaultdict(float)
    for r in rows:daily[r['day']]+=r['pnl']-.0025*r['quantity']
    vals=np.array(list(daily.values()));rng=np.random.default_rng(20261004)
    centered=vals-vals.mean();draws=100000
    simulated=centered[rng.integers(0,len(vals),(draws,len(vals)))].sum(axis=1)
    raw=float(np.mean(simulated>=vals.sum()))
    result.update(one_sided_centered_day_bootstrap_p=raw,bonferroni_32_routes=min(1,raw*32),bonferroni_1263_total_screens=min(1,raw*1263))
    return result

def capital(rows,initial=100):
    # Debit at entry, release actual settlement; no cap that silently drops losers.
    events=[]
    for r in rows:
        events.append((r['entered'],1,-r['quantity']*r['entry']-r['fee']-.0025*r['quantity']))
        events.append((r['exit_ts'],0,r['quantity']*r['exit']))
    cash=initial;low=cash
    for t,kind,value in sorted(events):cash+=value;low=min(low,cash)
    return dict(initial_cash=initial,min_available_cash=low,ending_cash=cash,additional_trades_skipped=0,
        note='Standalone cash bookkeeping, no daily stop; not current live shared portfolio.')

if __name__=='__main__':
    ms=pickle.loads((P/'prepared.pickle').read_bytes());pairs=collections.defaultdict(dict)
    for m in ms:pairs[m['close']][m['asset']]=m
    bb=vc.load_books();out={};ledgers={}
    for route in ('BTC_priority','best'):
        out[route]={};ledgers[route]={}
        for mode in ('next_open','worse_signal_open','delay60','book','book_delay10'):
            rows=[];misses=collections.Counter();skipped=[]
            for cl,pair in sorted(pairs.items()):
                if mode.startswith('book') and s.cohort(cl) not in ('fit','selection'):continue
                tr,why=run(pair,route,mode,bb)
                misses.update(why)
                if tr:rows.append(tr)
                else:skipped.append(cl)
            ledgers[route][mode]=rows;out[route][mode]={}
            for co in ('fit','selection','test','supplement'):
                rr=[r for r in rows if r['cohort']==co];den=sum(s.cohort(cl)==co for cl in pairs)
                metrics={str(st):s.metrics(rr,den,st) for st in (0,.0025,.005,.01)}
                metrics['assets']=dict(collections.Counter(r['asset'] for r in rr));metrics['deadline_fallback']=s.metrics([r for r in rr if r['forced_fallback']],den)
                metrics['uncertainty']=uncertainty(rr) if rr else None
                out[route][mode][co]=metrics
            out[route][mode]['misses']=dict(misses);out[route][mode]['skipped_closes']=skipped;out[route][mode]['cash']=capital(rows)
            print(route,mode,[(co,out[route][mode][co]['0.0025']['n'],out[route][mode][co]['0.0025']['net']) for co in ('fit','selection','test','supplement')],flush=True)
        # Recompute order fees at five contracts; same frozen one-contract signals.
        five=[]
        for r in ledgers[route]['next_open']:
            q=dict(r);n=5;fee=s.fee(r['entry'],n);q.update(quantity=n,fee=fee,pnl=n*(r['exit']-r['entry'])-fee);five.append(q)
        out[route]['five_contract_quoted']={co:s.metrics([r for r in five if r['cohort']==co],sum(s.cohort(cl)==co for cl in pairs)) for co in ('fit','selection','test','supplement')}
    s.save('routing_validation.json',out)
    with gzip.open(P/'routing_validation_trades.json.gz','wt') as h:json.dump(ledgers,h)
