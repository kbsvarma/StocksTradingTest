"""Fixed variants on newly pulled October3; no selection or parameter updates."""
from impulse import *
import designs,staged
fresh=P/'oct3_public';s.old.P=fresh;s.old.FOLDERS=[fresh]
markets=s.old.load_data()
for i,m in enumerate(markets):m['mid']=i
markets=impulse_features(augment(markets));(fresh/'features.pickle').write_bytes(pickle.dumps(markets,protocol=5))
start=s.old.ts('2026-10-03T00:00:00Z');s.BOUNDS=[start+i*86400 for i in range(5)];s.PERIODS=('october3','unused1','unused2','unused3')
rec,a,counts=s.opportunities(markets);(fresh/'quote_opportunities.pickle').write_bytes(pickle.dumps((rec,a),protocol=5))
designs.PRIOR=fresh;designs.gate=eligible
cache={('quote',.85,q):inputs(markets,'quote',.85,q) for q in [1,2,5]};c=staged.C
result=dict(frozen=json.loads((P/'oct3_frozen.json').read_text()),markets=len(markets),by_asset=dict(collections.Counter(m['asset'] for m in markets)),scenarios={});saved={}
for label,stress,capcost in [('primary',.01,False),('two_cent',.02,False),('limit_cost',.01,True)]:
    b=run(cache['quote',.85,5],stress=stress,capcost=capcost);j=run(cache['quote',.85,5],c,stress=stress,capcost=capcost);rr=dict(baseline=s.old.metrics(b,stress),impulse5=compare(j,b,stress));saved[label]=dict(baseline=b,impulse5=j)
    for qty in [1,2]:
        ev=staged.event_inputs(cache,qty,markets,None,'quote');jj=staged.simulate(ev,qty,stress,capcost);mm=staged.metrics(jj,stress);mm.update(delta_net=round(mm['net']-s.old.metrics(b,stress)['net'],5),extra_markets=mm['markets']-len(b));rr['starter'+str(qty)]=mm;saved[label]['starter'+str(qty)]=jj
    result['scenarios'][label]=rr
save('oct3_results.json',result);save('oct3_trades.json',saved);print(json.dumps(result,indent=2))
