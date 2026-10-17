"""Causal daily model updates; no live execution."""
import argparse, collections, gzip, json, pickle
import numpy as np
import adaptive as ad
import screen as s
P=s.P;HORIZONS=[14,28,10000];POLICIES=['fixed12','fixed8','fixed4','edge0_force','edge1c_force','edge2c_force','edge2c_skip']
def fit(markets):
    xx=[];yy=[];ww=[]
    for m in markets:
        fs=[m['new_features'][r] for r in s.TIMES if r in m['new_features'] and 'xrich' in m['new_features'][r]]
        for f in fs:
            x=np.array(f['xrich']);y=int(m['result']==1)
            xx.extend([x,np.r_[x[0],-x[1:]]]);yy.extend([y,1-y]);ww.extend([.5/len(fs)]*2)
    X=np.array(xx);y=np.array(yy);w=np.array(ww);scale=np.sqrt(np.average(X*X,axis=0,weights=w));scale[:2]=1;scale[scale<1e-8]=1;X/=scale
    coef=np.zeros(X.shape[1]);coef[1]=1;prior=coef.copy();pen=np.eye(len(coef))*10
    for _ in range(80):
        z=np.einsum('ij,j->i',X,coef);p=1/(1+np.exp(-np.clip(z,-30,30)))
        g=np.einsum('ij,i->j',X,w*(p-y))+np.einsum('ij,j->i',pen,coef-prior)
        h=np.einsum('ij,ik,i->jk',X,X,w*p*(1-p))+pen;step=np.linalg.solve(h,g);coef-=step
        if abs(step).max()<1e-9:break
    return dict(coef=coef.tolist(),scale=scale.tolist(),train_markets=len(markets),latest_settlement=max(m['settled'] for m in markets),earliest_close=min(m['close'] for m in markets))
def prepare(ms):
    out={};dates=sorted({m['close']//86400*86400 for m in ms if m['close']>=s.FIT})
    for a in ('BTC','ETH'):
        for memory in HORIZONS:
            key=f'{a}/{memory}';out[key]={}
            for day in dates:
                train=[m for m in ms if m['asset']==a and day-memory*86400<=m['close']<day and m['settled']<day]
                model=fit(train);assert model['latest_settlement']<day;out[key][str(day)]=model
            print('Fit daily models',key,len(dates),flush=True)
    s.save('online_models.json',out)
def run(m,model,policy,delay=False):
    coef=np.array(model['coef']);scale=np.array(model['scale'])
    rems=[int(policy[5:])] if policy.startswith('fixed') else list(range(14,0,-1))
    edge=0 if policy=='edge0_force' else .01 if policy=='edge1c_force' else .02
    for rem in rems:
        f=m['new_features'].get(rem)
        if not f or 'xrich' not in f:continue
        ff=dict(f);ff['prich']=float(1/(1+np.exp(-np.clip(np.einsum('i,i',np.array(f['xrich'])/scale,coef),-30,30))))
        dr,ev=ad.choice(ff,'rich')
        if not (policy.startswith('fixed') or ev>=edge or rem==1 and policy.endswith('force')):continue
        tr=s.trade(m,ff,dr,delay=delay)
        if tr:tr.update(rem=rem,modeled_edge=ev);return tr
    return None
def evaluate(ms,models,cohorts,chosen=None):
    result={};ledgers={}
    for a in ('BTC','ETH'):
        for memory in HORIZONS:
            for policy in POLICIES:
                key=f'{a}/{memory}/{policy}'
                if chosen is not None and key not in chosen:continue
                rr=[]
                for m in ms:
                    if m['asset']!=a or m['cohort'] not in cohorts:continue
                    day=str(m['close']//86400*86400);tr=run(m,models[f'{a}/{memory}'][day],policy)
                    if tr:rr.append(tr)
                ledgers[key]=rr
                result[key]={c:s.metrics([r for r in rr if r['cohort']==c],sum(m['asset']==a and m['cohort']==c for m in ms)) for c in cohorts}
                if 'selection' in cohorts:
                    for i,(lo,hi) in enumerate([(s.FIT,s.FIT+7*86400),(s.FIT+7*86400,s.SELECT)]):
                        result[key]['half'+str(i)]=s.metrics([r for r in rr if lo<=r['close']<hi],sum(m['asset']==a and lo<=m['close']<hi for m in ms))
    return result,ledgers
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['prepare','select','test']);a=ap.parse_args();ms=pickle.loads((P/'prepared.pickle').read_bytes())
    if a.mode=='prepare':prepare(ms)
    else:
        models=json.loads((P/'online_models.json').read_text());cohorts=['selection'] if a.mode=='select' else ['test','supplement']
        out,rr=evaluate(ms,models,cohorts);s.save('online_'+a.mode+'.json',out)
        with gzip.open(P/('online_'+a.mode+'_trades.json.gz'),'wt') as h:json.dump(rr,h)
        if a.mode=='select':
            eligible=[k for k,v in out.items() if all(v[c]['net']>0 and v[c]['coverage']>=.9 for c in ('half0','half1'))]
            best=sorted(eligible,key=lambda k:out[k]['selection']['net'],reverse=True)
            selected=[]
            for asset in ('BTC','ETH'):
                part=[k for k in best if k.startswith(asset+'/')];selected+=part[:1]
            s.save('online_frozen.json',dict(selected=selected,passing=best,trials=len(out)));print(json.dumps(json.loads((P/'online_frozen.json').read_text()),indent=2))
        else:
            frozen=json.loads((P/'online_frozen.json').read_text());print(json.dumps({k:out[k] for k in frozen['selected']},indent=2))
