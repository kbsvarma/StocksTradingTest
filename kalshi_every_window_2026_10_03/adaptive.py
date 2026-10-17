"""Fitted entry timing and edge-first/deadline policies, offline only."""
import argparse, collections, gzip, json, math, pickle
import numpy as np
import screen as s
P=s.P

def choice(f,kind):
    p=s.phi(f['z']) if kind=='spot' and 'z' in f else f.get('p'+kind)
    if p is None:return None
    ey=p-f['a']-s.fee(f['a'])-.0025;en=1-p-(1-f['b'])-s.fee(1-f['b'])-.0025
    return (1,ey) if ey>=en else (-1,en)
def xx(f):
    z=abs(f.get('z',0));mid=max(f['mid'],1-f['mid']);ch=choice(f,'rich')
    return np.array([1,mid-.75,(mid-.75)**2,min(z,6)/3,(ch[1] if ch else 0)*10])
def ridge(x,y):
    X=np.array(x);y=np.array(y);pen=np.eye(X.shape[1])*100;pen[0,0]=1
    return np.linalg.solve(np.einsum('ij,ik->jk',X,X)+pen,np.einsum('ij,i->j',X,y))
def train(markets):
    models={}
    for a in ('BTC','ETH'):
        mm=[m for m in markets if m['asset']==a and m['cohort']=='fit']
        for skip in (False,True):
            future={m['ticker']:0. for m in mm};coeff={}
            for rem in range(1,15):
                fs=[(m,m['new_features'].get(rem)) for m in mm]
                ready=[(m,f) for m,f in fs if f is not None and choice(f,'rich') is not None]
                coef=ridge([xx(f) for m,f in ready],[future[m['ticker']] for m,f in ready])
                coeff[rem]=coef.tolist()
                for m,f in ready:
                    dr,ed=choice(f,'rich');cont=float(np.einsum('i,i',xx(f),coef))
                    enter=(rem==1 and (not skip or ed>0)) or (rem>1 and ed>=max(0,cont) if skip else rem>1 and ed>=cont)
                    if enter:
                        tr=s.trade(m,f,dr)
                        if tr:future[m['ticker']]=tr['pnl']-.0025
            models[a+('/skip' if skip else '/force')]=coeff
    s.save('continuation_models.json',models);return models

def run(m,kind,edge=0,skip=False,models=None):
    for rem in range(14,0,-1):
        f=m['new_features'].get(rem)
        if f is None:continue
        ch=choice(f,'rich' if kind=='optimal' else kind)
        if ch is None:continue
        dr,ev=ch
        if kind=='optimal':
            cf=models[m['asset']+('/skip' if skip else '/force')];coef=cf.get(str(rem),cf.get(rem))
            cont=float(np.einsum('i,i',xx(f),coef))
            enter=(rem==1 and (not skip or ev>0)) or (rem>1 and ev>=max(0,cont) if skip else rem>1 and ev>=cont)
        else:enter=ev>=edge or (rem==1 and not skip)
        if enter:
            tr=s.trade(m,f,dr)
            if tr:tr['modeled_edge']=ev;tr['rem']=rem;return tr
    return None

def defs():
    return [(a,k,e,sk) for a in ('BTC','ETH') for k in ('market','rich','spot') for e in (0,.01,.02,.04) for sk in (False,True)]+[(a,'optimal',0,sk) for a in ('BTC','ETH') for sk in (False,True)]
def key(d):a,k,e,sk=d;return f'{a}/{k}/edge{e}/'+('skip' if sk else 'force')
def screen(markets,models,cohorts,definitions):
    out={};ledgers={}
    for d in definitions:
        a,k,e,sk=d;rows=[]
        for m in markets:
            if m['asset']!=a or m['cohort'] not in cohorts:continue
            tr=run(m,k,e,sk,models)
            if tr:rows.append(tr)
        out[key(d)]={c:s.metrics([r for r in rows if r['cohort']==c],sum(m['asset']==a and m['cohort']==c for m in markets)) for c in cohorts}
        ledgers[key(d)]=rows
    return out,ledgers

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['select','test']);args=ap.parse_args()
    markets=pickle.loads((P/'prepared.pickle').read_bytes())
    if args.mode=='select':
        models=train(markets);out,ledgers=screen(markets,models,['fit','selection'],defs());s.save('adaptive_selection.json',out)
        eligible=[k for k,v in out.items() if all(v[c]['net']>0 and v[c]['coverage']>=.9 for c in ('fit','selection'))]
        rank=sorted(eligible,key=lambda k:out[k]['selection']['net'],reverse=True)
        selected=[]
        for a in ('BTC','ETH'):
            rr=[k for k in rank if k.startswith(a+'/')];selected.extend(rr[:1])
        s.save('adaptive_frozen.json',dict(selected=selected,passing=rank,trials=len(out),top_diagnostic=sorted(out,key=lambda k:out[k]['selection']['net'],reverse=True)[:8]));print(json.dumps(json.loads((P/'adaptive_frozen.json').read_text()),indent=2))
    else:
        frozen=json.loads((P/'adaptive_frozen.json').read_text());models=json.loads((P/'continuation_models.json').read_text())
        out,ledgers=screen(markets,models,['test','supplement'],defs());s.save('adaptive_test_inventory.json',out)
        with gzip.open(P/'adaptive_test_trades.json.gz','wt') as h:json.dump(ledgers,h)
        print(json.dumps({k:out[k] for k in frozen['selected']},indent=2))
