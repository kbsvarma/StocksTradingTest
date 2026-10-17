"""Coverage check of every early robustness pass, without changing selection."""
import importlib.util,pickle,json
from pathlib import Path
P=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('frequency_research',P/'research.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
cache=pickle.loads((P/'inputs.pickle').read_bytes());out=[]
for item in json.loads((P/'early_ranking.json').read_text()):
    if not item['robust']:continue
    c=item['config'];rec,a=cache['quote',c['cap']];aa=r.s.subset(a,'later');base=r.run(rec,aa);joint=r.run(rec,aa,c)
    out.append(dict(config=c,later=r.compare(joint,base),stress2c=r.compare(r.run(rec,aa,c,stress=.02),r.run(rec,aa,stress=.02),.02),
        cap_plus1c=r.compare(r.run(rec,aa,c,capcost=True),r.run(rec,aa,capcost=True)),
        october2=r.compare(r.run(rec,r.s.subset(a,'october2'),c),r.run(rec,r.s.subset(a,'october2')))))
(P/'all_early_passes_later.json').write_text(json.dumps(out,indent=2))
print('Early robust',len(out),'later positive',sum(x['later']['delta_net']>0 for x in out))
