"""Independent Decimal reconstruction of two raw captures vs compact transitions."""
from pathlib import Path
from decimal import Decimal as D
import gzip,json,sys,collections,hashlib
P=Path(sys.argv[1]);manifest=json.loads((P/'manifest.json').read_text());chosen=[manifest['manifest'][1],manifest['manifest'][-1]];report=[]
def top(levels):
    ordered=sorted(levels.items(),reverse=True);out=[float(ordered[0][0]) if ordered else None]
    for target in (D(1),D(5)):
        qty=D(0);cost=D(0);worst=None
        for p,n in ordered:
            take=min(n,target-qty);cost+=p*take;qty+=take;worst=p
            if qty==target:break
        out.extend([float(cost/target) if qty==target else None,float(worst) if qty==target else None])
    return out
for rec in chosen:
    with gzip.open(P/rec['target'],'rt') as f:compact=json.load(f)['rows']
    cursors=collections.defaultdict(int);books={};frames=0
    with gzip.open(rec['source'],'rt') as inp:
        for line in inp:
            if '"type":"orderbook_' not in line:continue
            r=json.loads(line);m=r['msg'];ticker=m['market_ticker'];now=r['client_received_at_ms'];kind=r['type'];frames+=1
            if kind=='orderbook_snapshot':books[ticker]={side:{D(p):D(n) for p,n in m.get(side+'_dollars_fp',[]) if D(n)>0} for side in ('yes','no')}
            else:
                side=m['side'];p=D(m['price_dollars']);n=books[ticker][side].get(p,D(0))+D(m['delta_fp']);assert n>=0
                if n==0:books[ticker][side].pop(p,None)
                else:books[ticker][side][p]=n
            i=cursors[ticker]
            if i>=len(compact[ticker]):continue
            expected=compact[ticker][i];assert expected[0]>=now,(ticker,'unmatched prior row',expected[0],now)
            if expected[0]>now:continue
            observed=top(books[ticker]['yes'])+top(books[ticker]['no'])
            if not 0<=now-r.get('sending_ts_ms',now)<=1000:observed=[None]*10
            same=all((a is None and b is None) or (a is not None and b is not None and abs(a-b)<1e-9) for a,b in zip(expected[1:],observed))
            if same:cursors[ticker]+=1
    assert all(cursors[t]==len(v) for t,v in compact.items()),cursors
    report.append(dict(source=rec['source'],source_sha256=rec['source_sha256'],frames=frames,verified_compact_rows=dict(cursors)))
out=dict(status='PASS',independent_decimal_reconstruction=report);(P.parent/'compact_audit.json').write_text(json.dumps(out,indent=2));print(json.dumps(out))
