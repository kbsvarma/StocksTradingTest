"""Independent checks of preferred rule and capacity-buffer experiment."""
import sys,importlib.util,json,pickle,collections
from pathlib import Path
P=Path(__file__).resolve().parent;PRIOR=P.parent/'kalshi_z_time_research_2026_10_03'
sys.path.insert(0,str(PRIOR));import study as s
sys.path.insert(0,str(P));import research as maybe_old
spec=importlib.util.spec_from_file_location('frequency_research',P/'research.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
spec=importlib.util.spec_from_file_location('prior_validation',PRIOR/'validate.py');v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
import numpy as np

cache=pickle.loads((P/'inputs.pickle').read_bytes());configs=json.loads((P/'configurations.json').read_text());c=next(c for c in configs if c['id']==558)
rec,a=cache['quote',.939];aa=s.subset(a,'later')
actual,blocks=v.actual_guard_replay(rec,aa,dict(profile=0,z=1.5,lo=6,hi=7,mask=3,slope=0))
rows=r.run(rec,aa,c);assert [x['ticker'] for x in actual]==[x['ticker'] for x in rows]
assert s.old.metrics(actual)==s.old.metrics(rows)
v.boundaries()
# A first experimental trade fits at zero P&L. A second one cannot consume the
# slot retained for an existing-lane trade. Actual settlement releases exposure.
def row(mid,asset,t,lane):return [mid,asset,0,lane,t,t+300,t+200,1,1,6,2.,.90,.939,1,s.fee(.90,5),mid]
arr=np.array([row(0,0,100,2),row(1,1,101,2),row(2,2,102,0),row(3,1,500,2)],float)
assert list(r.simulate_protected(arr,0,1.5,6,7,15,0,buffer=5))==[0,2,3]
assert list(r.simulate_protected(arr,0,1.5,6,7,15,0,buffer=0))==[0,1,3]
for (scenario,cap),(rec,aa) in cache.items():
    for x in aa:
        if x[s.LANE]==2:assert x[s.ENTRY]<=x[s.CAP]+1e-8 and x[s.CAP]<=cap+1e-8
# Continuous result, actual candidate attribution, missed-IOC sensitivity.
base=r.run(*cache['quote',.939]);joint=r.run(*cache['quote',.939],c)
miss=[];rec,aa=cache['quote',.939];aa=s.subset(aa,'later')
for seed in range(100):
    b=r.run(rec,aa,miss_rate=.25,miss_seed=seed);j=r.run(rec,aa,c,miss_rate=.25,miss_seed=seed)
    miss.append(r.compare(j,b)['delta_net'])
out=dict(production_guard_exact_match=True,metrics=s.old.metrics(actual),buffer_slot_test=True,all_cap_checks=True,
    continuous51=r.compare(joint,base),missed25pct=dict(mean_delta=float(np.mean(miss)),p05=float(np.quantile(miss,.05)),p95=float(np.quantile(miss,.95))),
    by_asset={asset:s.old.metrics([x for x in rows if x['lane']=='third' and x['asset']==asset]) for asset in ['BTC','ETH']})
(P/'validation.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
