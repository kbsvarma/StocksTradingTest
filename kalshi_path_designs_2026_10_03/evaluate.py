"""Early book selection, frozen primary, then later-period evaluation."""
from designs import *

def main():
    short=json.loads((P/'shortlist.json').read_text());markets=pickle.loads((P/'features.pickle').read_bytes());books=s.old.load_books()
    qcache=pickle.loads((P/'quote_inputs.pickle').read_bytes());cache={('quote',floor,5):d for floor,d in qcache.items()};chosen=short['candidates']
    floors=sorted({r['config']['floor'] for r in chosen})
    for floor in floors:
        for source in ['book','book10']:cache[source,floor,5]=inputs(markets,source,floor,5,books)
    early=[]
    for item in chosen:
        c=item['config'];r=dict(config=c,quote_early_gate=item['early_gate'],early_quote=item['early'],early_books={});deltas=[]
        for source in ['book','book10']:
            data=cache[source,c['floor'],5]
            for period in s.PERIODS[:2]:
                for stress in [.01,.02]:
                    b=run(data,period=period,stress=stress);j=run(data,c,period,stress=stress);m=compare(j,b,stress)
                    r['early_books'][source+'_'+period+'_'+str(stress)]=m;deltas.append(m['delta_net'])
        r['pass_all_early']=bool(item['early_gate'] and min(deltas)>0)
        r['selection_score']=min(deltas)+item['score'];early.append(r)
    early.sort(key=lambda r:(r['pass_all_early'],r['selection_score']),reverse=True)
    frozen=dict(ts=dt.datetime.now(dt.timezone.utc).isoformat(),primary=early[0]['config'],primary_passes_all_early=early[0]['pass_all_early'],candidates=early,shortlist_sha256=s.digest(P/'shortlist.json'),later_not_evaluated=True)
    save('primary_frozen.json',frozen)
    print('FROZEN PRIMARY',frozen['primary'],'PASS',frozen['primary_passes_all_early'],flush=True)
    out={};trades={}
    for item in early:
        c=item['config'];cid=str(c['id']);out[cid]={};trades[cid]={}
        for source in ['quote','book','book10']:
            data=cache[source,c['floor'],5];out[cid][source]={};trades[cid][source]={}
            for period in (('all',)+s.PERIODS if source=='quote' else ('all',)+s.PERIODS[:2]):
                result={}
                for label,stress,capcost in [('primary',.01,False),('two_cent',.02,False),('limit_cost',.01,True)]:
                    b=run(data,period=period,stress=stress,capcost=capcost);j=run(data,c,period,stress=stress,capcost=capcost)
                    m=compare(j,b,stress);m['baseline']=s.old.metrics(b,stress);result[label]=m
                    if label=='primary':
                        trades[cid][source][period]=j
                        m['standalone']=s.old.metrics(run(data,c,period,standalone=True),stress)
                out[cid][source][period]=result
        print('LATER',c,'m', {k:out[cid]['quote']['later']['primary'][k] for k in ['n','extra','net','delta_net','wins','losses','drawdown']},'2c',out[cid]['quote']['later']['two_cent']['delta_net'],'oct2',out[cid]['quote']['october2']['primary']['delta_net'],flush=True)
    save('results.json',out);(P/'selected_trades.pickle').write_bytes(pickle.dumps(trades,protocol=5));(P/'five_contract_inputs.pickle').write_bytes(pickle.dumps(cache,protocol=5))
    sizes={}
    for quantity in [1,2]:
        sizecache={}
        for source in ['quote','book','book10']:
            for floor in floors:sizecache[source,floor,quantity]=inputs(markets,source,floor,quantity,books if source!='quote' else None)
        sizes[str(quantity)]={}
        for item in early:
            c=item['config'];cid=str(c['id']);sizes[str(quantity)][cid]={}
            for source in ['quote','book','book10']:
                data=sizecache[source,c['floor'],quantity];sizes[str(quantity)][cid][source]={}
                for period in (('all',)+s.PERIODS if source=='quote' else ('all',)+s.PERIODS[:2]):
                    m={}
                    for label,stress,capcost in [('primary',.01,False),('two_cent',.02,False),('limit_cost',.01,True)]:
                        b=run(data,period=period,stress=stress,capcost=capcost);j=run(data,c,period,quantity=quantity,stress=stress,capcost=capcost)
                        m[label]=compare(j,b,stress)
                    sizes[str(quantity)][cid][source][period]=m
        (P/f'inputs_size{quantity}.pickle').write_bytes(pickle.dumps(sizecache,protocol=5))
        cid=str(frozen['primary']['id']);print('SIZE',quantity,'PRIMARY LATER',sizes[str(quantity)][cid]['quote']['later'],flush=True)
    save('quantity_sensitivity.json',sizes)

if __name__=='__main__':main()
