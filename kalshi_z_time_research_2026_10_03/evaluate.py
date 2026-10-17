"""Evaluate already frozen candidates; never selects using later performance."""
from study import *

def daily(rows,days,stress=.01):
    d=collections.defaultdict(float)
    for r in rows:d[old.day(r['settled'])]+=r['pnl']-stress*r['quantity']
    return np.array([d[x] for x in days])

def compare(rows,base,stress=.01):
    mm=old.metrics(rows,stress);bm=old.metrics(base,stress)
    bmset={r['ticker'] for r in base};rrset={r['ticker'] for r in rows}
    third=[r for r in rows if r['lane']=='third']
    changed=[r for r in rows if r['ticker'] in bmset and r['lane']=='third']
    mm.update(delta_net=round(mm['net']-bm['net'],5),delta_trades=len(rows)-len(base),new_markets=len(rrset-bmset),lost_baseline_markets=len(bmset-rrset),baseline_markets_taken_by_third=len(changed),third=old.metrics(third,stress),by_asset={a:old.metrics([r for r in third if r['asset']==a],stress) for a in ASSETS})
    return mm

def main():
    tag='expanded_' if '--expanded' in sys.argv else ''
    frozen=json.loads((P/(tag+'shortlist_frozen.json')).read_text());candidates=frozen['candidates']
    records,a=pickle.loads((P/'quote_opportunities.pickle').read_bytes());markets=pickle.loads((P/'features.pickle').read_bytes())
    baselines={};result={};saved={};controls={}
    for period in PERIODS:
        aa=subset(a,period);base=selected_rows(records,aa)
        baselines[period]=old.metrics(base);saved[period]={'baseline':base};result[period]={}
        if period=='later':
            reference={name:[] for name in ['current_signal95','premium_z3']}
            for r in aa:
                if r[LANE]<2:reference[list(reference)[int(r[LANE])]].append(records[int(r[OID])])
            original,_=reference_portfolio(reference,list(reference),20,19,10)
            assert [r['ticker'] for r in original]==[r['ticker'] for r in base]
            assert old.metrics(base)==old.metrics(original)
            assert len(base)==302 and abs(old.metrics(base)['net']-65.9172)<1e-8
            controls['prior_baseline_regression']='302 trades, 298 wins/4 losses, +65.9172; exact market list and metrics match reference replay'
        for c in candidates:
            rr=selected_rows(records,aa,c);key=str(c['id']);saved[period][key]=rr
            out=compare(rr,base)
            out['sensitivities']={}
            for label,kwargs in [('fee_only',{'stress':0}),('stress2c',{'stress':.02}),('cap_plus1c',{'capcost':True}),('whole_cent_fees',{'centfee':True}),('third_one_contract',{'third_n':1})]:
                b=selected_rows(records,aa,**kwargs);r=selected_rows(records,aa,c,**kwargs)
                out['sensitivities'][label]=compare(r,b,kwargs.get('stress',.01))
            result[period][key]=out
        print(period,'baseline',baselines[period],flush=True)
    save(tag+'baseline_validation.json',controls)
    save(tag+'quote_results.json',dict(shortlist_sha256=digest(P/(tag+'shortlist_frozen.json')),baselines=baselines,candidates=result))
    (P/(tag+'quote_selected_trades.pickle')).write_bytes(pickle.dumps(saved,protocol=5))
    # Primary results ordered by the frozen development/validation order, not later profit.
    for c in candidates[:12]:
        r=result['later'][str(c['id'])];print('LATER',c['id'],c['assets'],c['profile'],c['z'],c['lo'],c['hi'],c['slope'],r['delta_trades'],r['delta_net'],r['third'],flush=True)
    # Paired settlement-day block resampling; descriptive only, not adjusted for the entire search.
    boot={};days=[old.day(t) for t in range(BOUNDS[2],BOUNDS[3]+86400,86400)]
    rng=np.random.default_rng(20261003);ix=rng.integers(0,len(days),size=(20000,len(days)))
    for c in candidates:
        key=str(c['id']);v=daily(saved['later'][key],days)-daily(saved['later']['baseline'],days)
        sample=v[ix].sum(axis=1)
        boot[key]=dict(days=days,delta_by_day=v.tolist(),lower95=float(np.quantile(sample,.025)),upper95=float(np.quantile(sample,.975)),remove_best3_days=float(v.sum()-sum(sorted(v,reverse=True)[:3])),one_additional_5_contract_loss_delta=float(v.sum()-5),multiple_search_adjusted=False)
    save(tag+'incremental_bootstrap.json',boot)
    old.FOLDERS=FOLDERS;books=None;bookresults={};booksaved={}
    for label,delay,rechecks in [('books25',.3,True),('books25_delay10',10.,True),('books25_first',.3,False)]:
        cache=P/(label+'_opportunities.pickle')
        if cache.exists():rec,aa=pickle.loads(cache.read_bytes())
        else:
            if books is None:books=old.load_books()
            rec,aa,counts=opportunities(markets,books=books,delay=delay,rechecks=rechecks)
            cache.write_bytes(pickle.dumps((rec,aa),protocol=5))
        bookresults[label]={};booksaved[label]={}
        for period in PERIODS[:2]:
            arr=subset(aa,period);base=selected_rows(rec,arr);out={'baseline':old.metrics(base),'candidates':{}};booksaved[label][period]={'baseline':base}
            for c in candidates:
                rr=selected_rows(rec,arr,c);key=str(c['id']);out['candidates'][key]=compare(rr,base);booksaved[label][period][key]=rr
            bookresults[label][period]=out
        print(label,{p:bookresults[label][p]['baseline'] for p in PERIODS[:2]},flush=True)
    save(tag+'book_results.json',bookresults);(P/(tag+'book_selected_trades.pickle')).write_bytes(pickle.dumps(booksaved,protocol=5))
    # Full later grid is descriptive exploration only; cannot retroactively enter shortlist.
    cm=np.array([cargs(c) for c in json.loads((P/'configurations.json').read_text())],dtype=float)
    if not (P/'later_descriptive_screen.npy').exists():np.save(P/'later_descriptive_screen.npy',batch(subset(a,'later'),cm))
    print('DONE',flush=True)

if __name__=='__main__':main()
