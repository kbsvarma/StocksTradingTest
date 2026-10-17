"""All frozen impulse finalists at prespecified smaller experimental quantities."""
from impulse import *
import designs
designs.gate=eligible
frozen=json.loads((P/'impulse_frozen.json').read_text());markets=impulse_features(pickle.loads((P/'features.pickle').read_bytes()))
look={(m['ticker'],f['t']):f for m in markets for f in m['features']};results={};saved={}
for qty in [1,2]:
    cache=pickle.loads((P/f'inputs_size{qty}.pickle').read_bytes())
    for rec,a,fs in cache.values():
        for oid in fs:fs[oid]=look[rec[oid]['ticker'],rec[oid]['t']]
    results[str(qty)]={};saved[str(qty)]={}
    for item in frozen['candidates']:
        c=item['config'];cid=str(c['id']);results[str(qty)][cid]={};saved[str(qty)][cid]={}
        for source in ['quote','book','book10']:
            data=cache[source,c['floor'],qty];results[str(qty)][cid][source]={};saved[str(qty)][cid][source]={}
            for co in (('all',)+s.PERIODS if source=='quote' else ('all',)+s.PERIODS[:2]):
                mm={}
                for label,stress,capcost in [('primary',.01,False),('two_cent',.02,False),('limit_cost',.01,True)]:
                    b=run(data,period=co,stress=stress,capcost=capcost);j=run(data,c,co,quantity=qty,stress=stress,capcost=capcost);mm[label]=compare(j,b,stress)
                    if label=='primary':
                        saved[str(qty)][cid][source][co]=j
                        mm[label]['standalone']=s.old.metrics(run(data,c,co,quantity=qty,standalone=True))
                results[str(qty)][cid][source][co]=mm
        print('SIZE',qty,cid,{k:results[str(qty)][cid]['quote']['later']['primary'][k] for k in ['n','extra','net','delta_net','drawdown']},'2c',results[str(qty)][cid]['quote']['later']['two_cent']['delta_net'],'oct2',results[str(qty)][cid]['quote']['october2']['primary']['delta_net'],flush=True)
save('impulse_sizes.json',results);(P/'impulse_size_trades.pickle').write_bytes(pickle.dumps(saved,protocol=5))
