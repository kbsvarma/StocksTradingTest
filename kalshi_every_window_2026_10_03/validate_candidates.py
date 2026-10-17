"""Stress frozen/secondary candidates without changing thresholds."""
import bisect, collections, gzip, hashlib, json, math, pickle
import numpy as np
import screen as s
import adaptive as ad
P=s.P
CANDIDATES=[('BTC','rich',.01,True),('ETH','rich',.02,True),('ETH','rich',.01,True)]

def load_books():
    path=P/'input_sources/books.jsonl.gz'
    if not path.exists():path=s.ROOT/'kalshi_restart_audit_2026_09_12/fresh_data/books.jsonl.gz'
    out=collections.defaultdict(list)
    for line in gzip.open(path,'rt'):
        b=json.loads(line)
        if b['ticker'].split('-')[0] not in ('KXBTC15M','KXETH15M'):continue
        out[b['ticker']].append((float(b['ts']),[(float(p),float(q)) for p,q in b['yes']],[(float(p),float(q)) for p,q in b['no']]))
    for rows in out.values():rows.sort()
    return out
def fill(book,direction,n=1):
    levels=sorted([(1-p,q) for p,q in book[2 if direction==1 else 1] if q>0])
    left=n;cost=0;fees=0
    for p,q in levels:
        if not 0<p<1:continue
        take=min(left,q);cost+=take*p;fees+=.07*p*(1-p)*take;left-=take
        if left<=1e-8:return cost/n,math.ceil(fees*10000-1e-8)/10000
    return None
def run(m,kind,edge,skip,mode,books=None,n=1):
    for rem in range(14,0,-1):
        f=m['new_features'].get(rem)
        if f is None:continue
        ch=ad.choice(f,kind)
        if ch is None:continue
        dr,ev=ch
        if not (ev>=edge or (rem==1 and not skip)):continue
        if mode.startswith('book'):
            rows=books.get(m['ticker'],[]);tt=[b[0] for b in rows]
            ix=bisect.bisect_left(tt,f['t']+(10 if mode=='book_delay10' else .3))
            if ix==len(rows) or tt[ix]>f['t']+35:continue
            got=fill(rows[ix],dr,n)
            if got is None:continue
            price,paid=got
            tr=dict(ticker=m['ticker'],asset=m['asset'],close=m['close'],day=s.day(m['close']),cohort=m['cohort'],
                decision=f['t'],entered=tt[ix],side=dr,entry=price,exit=int(m['result']==dr),exit_ts=max(m['close']+1,m['settled']),
                exit_reason='settlement',quantity=n,fee=paid,pnl=n*(int(m['result']==dr)-price)-paid,settlement_win=int(m['result']==dr),legs=1)
        else:
            tr=s.trade(m,f,dr,delay=mode=='delay60',quantity=n)
            if tr is None:continue
            if mode=='worse_signal_open':
                p=max(tr['entry'],f['a'] if dr==1 else 1-f['b']);tr.update(entry=p,fee=s.fee(p,n),pnl=n*(tr['exit']-p)-s.fee(p,n))
        tr.update(rem=rem,modeled_edge=ev);return tr
    return None

def uncertainty(rows,stress=.0025,draws=20000):
    dd=collections.defaultdict(float)
    for r in rows:dd[r['day']]+=r['pnl']-stress*r['quantity']
    vals=np.array(list(dd.values()));rng=np.random.default_rng(20261003)
    sums=vals[rng.integers(0,len(vals),(draws,len(vals)))].sum(axis=1) if len(vals) else np.zeros(draws)
    pp=sorted([r['pnl']-stress*r['quantity'] for r in rows],reverse=True)
    return dict(days=len(vals),bootstrap_total_95=np.percentile(sums,[2.5,97.5]).tolist(),probability_positive=float((sums>0).mean()),
        remove_5_best_trades=sum(pp[5:]),remove_best_day=float(vals.sum()-max(vals)) if len(vals) else 0,
        note='Retrospective day bootstrap, not multiple-search adjusted or confidence in future profitability.')

if __name__=='__main__':
    ms=pickle.loads((P/'prepared.pickle').read_bytes());bb=load_books();out={};ledger={}
    for d in CANDIDATES:
        a,k,e,sk=d;name=ad.key(d);out[name]={};ledger[name]={}
        for mode in ('next_open','worse_signal_open','delay60','book','book_delay10'):
            rows=[]
            for m in ms:
                if m['asset']!=a:continue
                if mode.startswith('book') and m['cohort'] not in ('fit','selection'):continue
                tr=run(m,k,e,sk,mode,bb)
                if tr:rows.append(tr)
            ledger[name][mode]=rows
            out[name][mode]={c:{str(st):s.metrics([r for r in rows if r['cohort']==c],sum(m['asset']==a and m['cohort']==c for m in ms),st) for st in (0,.0025,.005,.01)} for c in ('fit','selection','test','supplement')}
            out[name][mode]['uncertainty_test']=uncertainty([r for r in rows if r['cohort']=='test'])
        print(name,[(mode,[(c,v['0.0025']['n'],v['0.0025']['net']) for c,v in x.items() if c!='uncertainty_test']) for mode,x in out[name].items()],flush=True)
    s.save('secondary_validation.json',out)
    with gzip.open(P/'secondary_trades.json.gz','wt') as h:json.dump(ledger,h)
