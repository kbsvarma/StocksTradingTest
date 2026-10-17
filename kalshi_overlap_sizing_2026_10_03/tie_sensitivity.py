"""Post-result robustness diagnostic; do not optimize or select a tie ordering."""
from replay import *
import itertools
markets=pickle.loads((s.P/'features.pickle').read_bytes())
ev,_,_,_=events('quote',markets);out=[]
for order in itertools.permutations(s.ASSETS):
    rank={a:i for i,a in enumerate(order)}
    for reverse_lane in (False,True):
        ee=sorted(ev,key=lambda e:(e['entered'],-e['lane'] if reverse_lane else e['lane'],rank[e['asset']]))
        rr={}
        for period in ('all','later'):
            es=ee if period=='all' else [e for e in ee if s.BOUNDS[2]<=e['close']<s.BOUNDS[3]]
            b=replay(es,'baseline')[0];p=replay(es,'parallel5')[0]
            rr[period]=dict(baseline=metrics(b),parallel=metrics(p),delta_net=round(metrics(p)['net']-metrics(b)['net'],5),
                second_losses=sum(not r['won'] for r in p if r['second']))
        out.append(dict(asset_priority=order,z3_priority=reverse_lane,results=rr))
save('tie_sensitivity.json',dict(note='Post-result sensitivity, 24 asset orders x 2 lane orders. Same order applied to baseline and candidate. Quote timestamps cannot establish actual microsecond scheduling. No preferred ordering selected.',scenarios=out))
for period in ('all','later'):
    deltas=[x['results'][period]['delta_net'] for x in out]
    losses=[x['results'][period]['second_losses'] for x in out]
    print(period,'delta range',min(deltas),max(deltas),'positive',sum(d>0 for d in deltas),'of',len(deltas),'add-on loss range',min(losses),max(losses),flush=True)
