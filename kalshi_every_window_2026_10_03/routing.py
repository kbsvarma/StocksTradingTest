import argparse, collections, gzip, json, pickle
import numpy as np
import screen as s
import adaptive as ad
P=s.P
def forecast(m,f,kind,models):
    if kind=='frozen':return ad.choice(f,'rich')
    if 'xrich' not in f:return None
    model=models[m['asset']+'/14'][str(m['close']//86400*86400)];coef=np.array(model['coef']);scale=np.array(model['scale'])
    ff=dict(f);ff['prich']=float(1/(1+np.exp(-np.clip(np.einsum('i,i',np.array(f['xrich'])/scale,coef),-30,30))))
    return ad.choice(ff,'rich')
def run(pair,kind,route,e,skip,models):
    for rem in range(14,0,-1):
        offers=[]
        for a in ('BTC','ETH'):
            m=pair.get(a)
            if m is None:continue
            f=m['new_features'].get(rem)
            if f is None:continue
            ch=forecast(m,f,kind,models)
            if ch is not None:offers.append((m,f,ch))
        if route=='best':offers.sort(key=lambda x:x[2][1],reverse=True)
        eligible=[o for o in offers if o[2][1]>=e]
        if not eligible and rem==1 and not skip:eligible=offers
        for m,f,ch in eligible:
            tr=s.trade(m,f,ch[0])
            if tr:tr.update(rem=rem,modeled_edge=ch[1]);return tr
    return None
if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['select','test']);args=ap.parse_args()
    ms=pickle.loads((P/'prepared.pickle').read_bytes());models=json.loads((P/'online_models.json').read_text());pairs=collections.defaultdict(dict)
    cos=['selection'] if args.mode=='select' else ['test','supplement']
    for m in ms:
        if m['cohort'] in cos:pairs[m['close']][m['asset']]=m
    out={};ledgers={}
    for kind in ('frozen','online14'):
        for route in ('BTC_priority','best'):
            for e in (0,.01,.02,.04):
                for skip in (False,True):
                    key=f'{kind}/{route}/{e}/'+('skip' if skip else 'force');rows=[]
                    for cl,pair in sorted(pairs.items()):
                        tr=run(pair,kind,route,e,skip,models)
                        if tr:rows.append(tr)
                    ledgers[key]=rows;out[key]={c:s.metrics([r for r in rows if r['cohort']==c],sum(s.cohort(t)==c for t in pairs)) for c in cos}
                    for c in cos:out[key][c]['assets']=dict(collections.Counter(r['asset'] for r in rows if r['cohort']==c))
                    if args.mode=='select':
                        for i,(lo,hi) in enumerate([(s.FIT,s.FIT+7*86400),(s.FIT+7*86400,s.SELECT)]):out[key]['half'+str(i)]=s.metrics([r for r in rows if lo<=r['close']<hi],sum(lo<=t<hi for t in pairs))
    s.save('routing_'+args.mode+'.json',out)
    with gzip.open(P/('routing_'+args.mode+'_trades.json.gz'),'wt') as h:json.dump(ledgers,h)
    if args.mode=='select':
        eligible=[k for k,v in out.items() if all(v[c]['net']>0 and v[c]['coverage']>=.9 for c in ('half0','half1'))]
        eligible.sort(key=lambda k:out[k]['selection']['net'],reverse=True)
        s.save('routing_frozen.json',dict(selected=eligible[:1],passing=eligible,trials=len(out)));print(json.dumps(json.loads((P/'routing_frozen.json').read_text()),indent=2))
    else:
        f=json.loads((P/'routing_frozen.json').read_text());print(json.dumps({k:out[k] for k in f['selected']},indent=2))
