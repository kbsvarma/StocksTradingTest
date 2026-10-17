"""Initial size reduction when risk capacity blocks five; no weaker signals."""
from designs import *
sys.path.insert(0,str(ROOT/'kalshi_overlap_sizing_2026_10_03'));import replay as ov
from flex_risk import flex_replay

def main():
    markets=pickle.loads((PRIOR/'features.pickle').read_bytes());books=s.old.load_books();allout={};trades={};checks=[]
    for source in ['quote','books25','books25_delay10']:
        ev,rec,a,_=ov.events(source,markets,books if source!='quote' else None)
        for e in ev:
            if source=='quote':
                for n in (1,2,3):e['fills'][n]=(e['entry'],s.fee(e['entry'],n))
            else:
                bb=books[e['ticker']];j=bisect.bisect_left([b[0] for b in bb],e['entered']);book=bb[j]
                for n in (1,2,3):e['fills'][n]=s.book_fill(book,e['side'],e['cap'],s.PROFILES[e['profile']].lo,n)
        allout[source]={};trades[source]={}
        for co in (('all',)+s.PERIODS if source=='quote' else ('all',)+s.PERIODS[:2]):
            ee=ev if co=='all' else [e for e in ev if s.BOUNDS[s.PERIODS.index(co)]<=e['close']<s.BOUNDS[s.PERIODS.index(co)+1]]
            allout[source][co]={};trades[source][co]={}
            for label,stress,capcost in [('primary',.01,False),('two_cent',.02,False),('limit_cost',.01,True)]:
                mm={}
                for policy in ['baseline','fallback2','fallback1','flex']:
                    rows,counts,maxima=flex_replay(ee,policy,stress,capcost);metric=ov.metrics(rows,stress)
                    if policy=='baseline':
                        baseline=rows;old=ov.replay(ee,stress=stress,capcost=capcost)[0]
                        assert rows==old;checks.append([source,co,label,len(rows)])
                    metric.update(delta_net=round(metric['net']-ov.metrics(baseline,stress)['net'],5),extra=len(rows)-len(baseline),sizing=dict(collections.Counter(r['quantity'] for r in rows)),counts=counts,maxima=maxima)
                    mm[policy]=metric
                    if label=='primary':trades[source][co][policy]=rows
                allout[source][co][label]=mm
            print('FLEX',source,co,{p:(m['n'],m['net'],m['delta_net']) for p,m in allout[source][co]['primary'].items()},flush=True)
    save('flex_results.json',allout);save('flex_validation.json',dict(baseline_parity=checks));(P/'flex_trades.pickle').write_bytes(pickle.dumps(trades,protocol=5))

if __name__=='__main__':main()
