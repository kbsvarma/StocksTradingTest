"""Reproduce frozen October9 controls, without writing into their archive."""
import importlib.util,json,pickle,sys
from pathlib import Path
P=Path(__file__).resolve().parent
OLD=P.parent/'kalshi_market_expansion_2026_10_09'
spec=importlib.util.spec_from_file_location('old_analysis',OLD/'analyze.py')
old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
r=old.r
assert (OLD/'extension/features.pickle').exists()
markets=pickle.loads((OLD/'extension/features.pickle').read_bytes())
expected=json.loads((OLD/'noncrypto_results.json').read_text())
new=pickle.loads((P/'features.pickle').read_bytes())
nf={(m['ticker'],f['t']):f for m in new for f in m['features']}
matches=0;max_error=0.;checks=[]
for a in ('USDJPY','GBPUSD','AUDUSD','USDCAD'):
    ms=[m for m in markets if m['asset']==a]
    for m in ms:
        for f in m['features']:
            n=nf.get((m['ticker'],f['t']))
            assert n is not None,(m['ticker'],f['t'])
            assert all(abs(n[k]-f[k])<1e-8 for k in ('ref','spot','ask','bid','sigma','z'))
            max_error=max(max_error,abs(n['z']-f['z']));matches+=1
    for profile in ('standard','eurusd_transfer'):
        r.qualifies=old.rule(profile)
        for label,mode,stress in old.SCENARIOS:
            pool,_=r.opportunities(ms,mode)
            ledger,_=r.portfolio([x for x in pool if x['close']<old.END],stress)
            got=r.metrics(ledger);want=expected[a+'/'+profile][label]['periods']['full']
            check={k:abs(got[k]-want[k])<1e-6 for k in ('trades','wins','losses','net','settled_drawdown')}
            assert all(check.values()),(a,profile,label,got,want)
            checks.append(dict(asset=a,profile=profile,scenario=label,checks=check,net=got['net']))
(P/'control_reproduction.json').write_text(json.dumps(dict(passed=True,checks=checks,
    matched_features=matches,max_z_error=max_error),indent=2)+'\n')
print('Control reproduction PASS',len(checks),'scenarios;',matches,'features',max_error)
