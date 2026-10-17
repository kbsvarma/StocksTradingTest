"""Past-only residual probability model with strong market-prior shrinkage."""
from core import *
from financial_replay import candidates,PERIODS,GROUPS
import argparse,pickle,math
import numpy as np
ASSETS=GROUPS['new_all']
def logit(p):return math.log(max(.001,p)/max(.001,1-p))
def variables(r):
    f=r['f'];mid=(f['ybid']+f['yask'])/2
    continuous=[np.clip(-f['threshold'],-6,6),logit(mid),np.clip(f['mom3'],-6,6),math.log(max(.01,min(4,f['volratio']))),min(10,f['jump'])]
    return continuous,logit(mid)
def prepare():
    rows=[r for r in pickle.load(open(P/'financial_features.pickle','rb')) if r['asset'] in ASSETS]
    for r in rows:r.pop('prob',None)
    audits=[];weeks=sorted({(r['f']['t']-345600)//604800*604800+345600 for r in rows})
    for week in weeks:
        train=[r for r in rows if r['settled']<week];test=[r for r in rows if week<=r['f']['t']<week+604800]
        if len(train)<500 or not test:continue
        counts=C.Counter(r['asset'] for r in train);test=[r for r in test if counts[r['asset']]>=250]
        if not test:continue
        raw=[variables(r) for r in train];rawx=np.array([r[0] for r in raw]);offset=np.array([r[1] for r in raw]);y=np.array([r['result'] for r in train])
        mu=rawx.mean(0);sd=rawx.std(0);sd[sd<1e-9]=1
        def X(rr):
            raw=np.array([variables(r)[0] for r in rr]);asset=np.array([[float(r['asset']==a) for a in ASSETS] for r in rr])
            return np.column_stack([asset,(raw-mu)/sd])
        x=X(train);beta=np.zeros(x.shape[1]);penalty=np.eye(len(beta))*20
        for iteration in range(60):
            eta=np.clip(offset+x@beta,-30,30);p=1/(1+np.exp(-eta));w=np.maximum(p*(1-p),1e-8)
            gradient=x.T@(p-y)+penalty@beta;h=x.T@(x*w[:,None])+penalty;step=np.linalg.solve(h,gradient);beta-=step
            if max(abs(step))<1e-9:break
        else:raise ValueError('fit did not converge')
        z=np.array([variables(r)[1] for r in test])+X(test)@beta;pred=1/(1+np.exp(-np.clip(z,-30,30)))
        for r,p in zip(test,pred):r['prob']=float(p)
        audits.append(dict(week=week,n=len(train),counts=counts,last_settlement=max(r['settled'] for r in train),test=len(test),
                           beta=beta.tolist(),mean=mu.tolist(),scale=sd.tolist(),ridge=20,iterations=iteration+1))
    (P/'conditional_features.pickle').write_bytes(pickle.dumps(rows));save('conditional_model_audit.json',audits)
    print('CONDITIONAL MODELS',len(audits),flush=True)
def run(mode):
    rows=pickle.load(open(P/'conditional_features.pickle','rb'));result={};logs={}
    periods=['earlier'] if mode=='select' else ['later','continuous']
    for qty in (1,5):
        name=f'q{qty}';result[name]={};logs[name]={}
        for scenario,book,cost in [('base','base',.01),('cost2','base',.02),('cost3','base',.03),('cap','cap',.01),('all_cap','all_cap',.01),('delay60','delay60',.01),('delay60_cost2','delay60',.02)]:
            pool,diag=candidates(rows,.1,qty,book);result[name][scenario]={};logs[name][scenario]={}
            for group in ('nonmetal','new_all'):
                result[name][scenario][group]={};logs[name][scenario][group]={}
                for period in periods:
                    lo,hi=PERIODS[period];eligible=[r for r in pool if r['asset'] in GROUPS[group] and lo<=r['t']<hi]
                    ledger,account=portfolio(eligible,qty,cost);m=metrics(ledger,(hi-lo)/86400);m.update(account)
                    m.update(qualified_proxy_candidates=len(eligible),candidate_diagnostics_all_assets=diag)
                    result[name][scenario][group][period]=m;logs[name][scenario][group][period]=ledger
        print(mode,name,{g:{p:{k:result[name]['base'][g][p][k] for k in ('trades','net','trades_per_day')} for p in periods} for g in ('nonmetal','new_all')},flush=True)
    save('conditional_'+mode+'_results.json',result);save('conditional_'+mode+'_ledgers.json.gz',logs)
    if mode=='select':
        passing=[]
        for n in result:
            for g in ('nonmetal','new_all'):
                x=result[n]
                if x['base'][g]['earlier']['trades_per_day']>=10 and all(x[s][g]['earlier']['net']>0 for s in ('base','cost2','cap','delay60_cost2')):passing.append([n,g])
        save('conditional_selection_frozen.json',dict(at=dt.datetime.now(dt.timezone.utc).isoformat(),passing=passing,later_not_evaluated=True,source_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    else:assert (P/'conditional_selection_frozen.json').exists()
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['prepare','select','evaluate']);args=parser.parse_args()
    prepare() if args.mode=='prepare' else run(args.mode)
