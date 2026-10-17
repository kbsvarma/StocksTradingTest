"""Paired zero-fill IOC sensitivity; failed attempt consumes the market."""
from study import *
records,a=pickle.loads((P/'quote_opportunities.pickle').read_bytes());a=subset(a,'later')
cs={c['id']:c for c in json.loads((P/'configurations.json').read_text())};out={}
for cid in [25301,25169,9220,7214]:
    trials=[]
    for seed in range(100):
        base=selected_rows(records,a,miss_rate=.25,miss_seed=seed);joint=selected_rows(records,a,cs[cid],miss_rate=.25,miss_seed=seed)
        trials.append(dict(seed=seed,base_n=len(base),joint_n=len(joint),extra=len(joint)-len(base),delta_net=old.metrics(joint)['net']-old.metrics(base)['net']))
    vals=[r['delta_net'] for r in trials]
    out[str(cid)]=dict(miss_rate=.25,mean_delta_net=float(np.mean(vals)),p05_delta_net=float(np.quantile(vals,.05)),p95_delta_net=float(np.quantile(vals,.95)),mean_extra_trades=float(np.mean([r['extra'] for r in trials])),trials=trials)
save('missed_fill_sensitivity.json',out)
print({k:{x:v[x] for x in v if x!='trials'} for k,v in out.items()})
