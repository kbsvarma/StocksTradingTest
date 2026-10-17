"""Report-ready comparisons and descriptive block-resampling uncertainty."""
from core import *
import numpy as np
import math
def uncertainty(rows,lo,hi):
    dates=[day(t) for t in range(lo,hi,86400)];sums=C.defaultdict(float);counts=C.Counter()
    for r in rows:sums[day(r['entry_t'])]+=r['net'];counts[day(r['entry_t'])]+=1
    a=np.array([sums[d] for d in dates]);rng=np.random.default_rng(20261012);out={}
    for block in (1,7):
        sampled=[]
        for i in range(10000):
            starts=rng.integers(0,len(a),math.ceil(len(a)/block))
            indices=[(int(s)+j)%len(a) for s in starts for j in range(block)][:len(a)]
            sampled.append(float(a[indices].sum()))
        out[f'{block}_day_block_net95']=np.quantile(sampled,[.025,.975]).tolist()
    out.update(zero_entry_days=sum(counts[d]==0 for d in dates),median_daily_trades=float(np.median([counts[d] for d in dates])),
               distinct_days=len(dates),note='Descriptive resampling of realized ledger by entry date; no refit or cash-path replay. Not corrected for strategy searches/reused history; few independent weeks.')
    return out
def main():
    summary={};unc={}
    p=read(P/'props_evaluate_results.json');logs=read(P/'props_evaluate_ledgers.json.gz')
    for rule in p:
        summary['props_'+rule]=dict(continuous=p[rule]['base']['continuous'],earlier=read(P/'props_select_results.json')[rule]['base']['selection'],
                                    later=p[rule]['base']['later'],cost2=p[rule]['cost2']['continuous'],delay60_cost2=p[rule]['delay60_cost2']['continuous'])
        unc['props_'+rule]=uncertainty(logs[rule]['base']['continuous'],ts('2026-09-15T04:00:00Z'),ts('2026-10-12T04:00:00Z'))
    f=read(P/'financial_evaluate_results.json');fl=read(P/'financial_evaluate_ledgers.json.gz');early=read(P/'financial_select_results.json')
    for rule in f:
        for group in f[rule]['base']:
            k='financial_'+rule+'_'+group
            summary[k]={s:f[rule][s][group]['continuous'] for s in f[rule]}
            summary[k].update(earlier=early[rule]['base'][group]['earlier'],later=f[rule]['base'][group]['later'])
            unc[k]=uncertainty(fl[rule]['base'][group]['continuous'],ts('2026-09-14T04:00:00Z'),ts('2026-10-12T04:00:00Z'))
    sports=read(P/'sports_evaluate_results.json')
    summary['sports_all12']={k:v for k,v in sports.items() if '/base/' in k and k.endswith('cost1')}
    f=read(P/'conditional_evaluate_results.json');fl=read(P/'conditional_evaluate_ledgers.json.gz');early=read(P/'conditional_select_results.json')
    for rule in f:
        for group in f[rule]['base']:
            k='conditional_'+rule+'_'+group
            summary[k]={s:f[rule][s][group]['continuous'] for s in f[rule]}
            summary[k].update(earlier=early[rule]['base'][group]['earlier'],later=f[rule]['base'][group]['later'])
            unc[k]=uncertainty(fl[rule]['base'][group]['continuous'],ts('2026-09-14T04:00:00Z'),ts('2026-10-12T04:00:00Z'))
    series=read(P/'data/series_now.json')['response']['series']
    save('catalog_summary.json',dict(series=len(series),categories=C.Counter(s.get('category') for s in series),
        active_by_lifetime_volume_not_current_depth=sum(float(s.get('volume_fp') or 0)>0 for s in series),
        note='Series inventory is NOT a backtest or current executable volume. New replays are only the named sports, props and financial families.'))
    save('comparison.json',summary);save('uncertainty.json',unc)
    print('SUMMARY',len(summary),'UNCERTAINTY',len(unc))
if __name__=='__main__':main()
