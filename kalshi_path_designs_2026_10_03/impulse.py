"""Second-pass actual price impulse; preserves failed first pass."""
from designs import *

def impulse_features(markets):
    for m in markets:
        fs={f['t']:f for f in m['features']}
        for f in m['features']:
            f['impulse1']=f['impulse2']=-99.;f['previous_clock_adjusted_z']=99.
            p=fs.get(f['t']-60);p2=fs.get(f['t']-120)
            if p and p['side']==f['side']:
                f['impulse1']=f['side']*math.log(f['spot']/p['spot'])/p['sigma']
                f['previous_clock_adjusted_z']=f['side']*(p['spot']/f['ref']-1)/(p['sigma']*math.sqrt(f['rem']))
                if p2 and p2['side']==f['side']:f['impulse2']=f['side']*math.log(f['spot']/p2['spot'])/(p2['sigma']*math.sqrt(2))
    return markets

def configurations():
    cc=[]
    for asset,mask in [('BTC',1),('ETH',2),('BTC_ETH',3)]:
        for floor in [.75,.85]:
            for lo,hi in [(3,10),(4,8),(5,7),(6,7)]:
                for z in [1.,1.25,1.5,1.75]:
                    for kind in ['impulse1','impulse2','crossing']:
                        for strength in [.25,.5,1.]:cc.append(dict(id=len(cc),family=kind,assets=asset,mask=mask,floor=floor,lo=lo,hi=hi,z=z,strength=strength))
    return cc

def eligible(c,f,asset):
    if asset not in ('BTC','ETH') or not ((1<<s.ASSETS.index(asset))&c['mask']):return False
    if not c['lo']<=f['rem']<=c['hi'] or f['z']<c['z'] or not c['floor']-1e-8<=f['ask']<.95-1e-8:return False
    if c['family']=='crossing':return f['impulse1']>=c['strength'] and f['previous_clock_adjusted_z']<c['z']
    return f[c['family']]>=c['strength']

def main():
    import designs
    designs.gate=eligible
    cc=configurations();save('impulse_configurations.json',cc)
    markets=impulse_features(pickle.loads((P/'features.pickle').read_bytes()));cache=pickle.loads((P/'five_contract_inputs.pickle').read_bytes())
    look={(m['ticker'],f['t']):f for m in markets for f in m['features']}
    for rec,a,fs in cache.values():
        for oid in fs:fs[oid]=look[rec[oid]['ticker'],rec[oid]['t']]
    ranked=[]
    for c in cc:
        data=cache['quote',c['floor'],5];rr={};deltas=[];growth=[];good=[]
        for period in s.PERIODS[:2]:
            for stress in [.01,.02]:
                b=run(data,period=period,stress=stress);j=run(data,c,period,stress=stress);m=compare(j,b,stress);rr[period+str(stress)]=m;deltas.append(m['delta_net'])
                if stress==.01:growth.append(m['extra']/len(b));good.append(m['third'].get('net',0)>0 and m['drawdown']<=s.old.metrics(b,stress)['drawdown']+10)
        ranked.append(dict(config=c,early=rr,gate=bool(min(deltas)>0 and min(growth)>=.25 and all(good)),score=min(deltas)+.15*sum(deltas),growth=min(growth)))
    ranked.sort(key=lambda r:(r['gate'],r['score']),reverse=True);save('impulse_early_ranking.json',ranked)
    chosen=[]
    for kind in ['impulse1','impulse2','crossing']:
        pool=[r for r in ranked if r['config']['family']==kind];chosen.append(pool[0])
        chosen.append(next(r for r in pool if r['config']['assets']!=pool[0]['config']['assets'] or r['config']['floor']!=pool[0]['config']['floor']))
        for asset in ['BTC','ETH','BTC_ETH']:chosen.append(next(r for r in pool if r['config']['assets']==asset))
    chosen=list({x['config']['id']:x for x in chosen}.values())
    for item in chosen:
        c=item['config'];deltas=[];item['book']={}
        for source in ['book','book10']:
            data=cache[source,c['floor'],5]
            for co in s.PERIODS[:2]:
                b=run(data,period=co);j=run(data,c,co);mm=compare(j,b);item['book'][source+co]=mm;deltas.append(mm['delta_net'])
        item['book_gate']=bool(item['gate'] and min(deltas)>0);item['book_score']=min(deltas)+item['score']
    chosen.sort(key=lambda r:(r['book_gate'],r['book_score']),reverse=True)
    frozen=dict(ts=dt.datetime.now(dt.timezone.utc).isoformat(),pass2_plan_sha256=s.digest(P/'PASS2.md'),trials=len(cc),early_gate_passes=sum(r['gate'] for r in ranked),primary=chosen[0]['config'],primary_pass=chosen[0]['book_gate'],candidates=chosen)
    save('impulse_frozen.json',frozen);print('FROZEN',frozen['primary'],'PASS',frozen['primary_pass'],flush=True)
    out={};trades={}
    for item in chosen:
        c=item['config'];cid=str(c['id']);out[cid]={};trades[cid]={}
        for source in ['quote','book','book10']:
            data=cache[source,c['floor'],5];out[cid][source]={};trades[cid][source]={}
            for co in (('all',)+s.PERIODS if source=='quote' else ('all',)+s.PERIODS[:2]):
                mm={}
                for label,stress,capcost in [('primary',.01,False),('two_cent',.02,False),('limit_cost',.01,True)]:
                    b=run(data,period=co,stress=stress,capcost=capcost);j=run(data,c,co,stress=stress,capcost=capcost);mm[label]=compare(j,b,stress)
                    if label=='primary':trades[cid][source][co]=j;mm[label]['standalone']=s.old.metrics(run(data,c,co,standalone=True))
                out[cid][source][co]=mm
        print('LATER',c,{k:out[cid]['quote']['later']['primary'][k] for k in ['n','extra','net','delta_net','drawdown','losses']},'2c',out[cid]['quote']['later']['two_cent']['delta_net'],'oct2',out[cid]['quote']['october2']['primary']['delta_net'],flush=True)
    save('impulse_results.json',out);(P/'impulse_trades.pickle').write_bytes(pickle.dumps(trades,protocol=5));(P/'impulse_inputs.pickle').write_bytes(pickle.dumps(cache,protocol=5))

if __name__=='__main__':main()
