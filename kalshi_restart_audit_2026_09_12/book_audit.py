"""Audit frozen shortlisted entries against subsequent recorded books.

Snapshot availability is not fill proof. No maker queue credit is inferred.
"""
import bisect
from collections import defaultdict
import gzip
import json
from pathlib import Path
from research import ROOT,DATA,fee,metrics,load_markets,rules,quote,side_quote,bootstrap

def executable_ask(row,side,size=1.):
    # Each side stores bids for that outcome. Buy YES against NO bids.
    levels=row.get('no' if side==1 else 'yes') or []
    levels=sorted([(1-float(p),float(n)) for p,n in levels],key=lambda x:x[0])
    remaining=size;cost=0
    for price,count in levels:
        if not 0<=price<=1 or count<=0:continue
        got=min(remaining,count);remaining-=got;cost+=got*price
        if remaining<1e-9:return cost/size,price
    return None

def main():
    selected=list(dict.fromkeys(json.loads((ROOT/'shortlist.json').read_text())+[
        'r1_deployed','cushion_coinbase_z1.5','cushion_benchmark_z1.5']))
    rows=[json.loads(l) for l in (ROOT/'trades.jsonl').read_text().splitlines()]
    rows=[r for r in rows if r['rule'] in selected]
    tickers={r['ticker'] for r in rows}
    books=defaultdict(list)
    with gzip.open(DATA/'books.jsonl.gz','rt') as f:
        for line in f:
            r=json.loads(line)
            if r['ticker'] in tickers:books[r['ticker']].append(r)
    for rr in books.values():rr.sort(key=lambda r:r['ts'])
    markets={m['ticker']:m for m in load_markets()}
    registry={r.name:r for r in rules()}
    result=[]
    for rule in selected:
        for sp in ('discovery','validation','test'):
            chosen=[r for r in rows if r['rule']==rule and r['split']==sp]
            available=0;valid=[];delays=[];caps=0;missing_exit=0
            for row in chosen:
                bb=books[row['ticker']]; times=[x['ts'] for x in bb]
                i=bisect.bisect_left(times,row['ts']+.3)
                if i==len(bb) or bb[i]['ts']>row['ts']+35:continue
                available+=1;ex=executable_ask(bb[i],row['side'])
                if ex is None:continue
                price,worst=ex
                # The minute replay entry is itself bounded by the original
                # signal. Use the stored signal ask, not a hindsight fill.
                cap=min(.999,row['signal_ask']+.01)
                if worst>cap+1e-8:caps+=1;continue
                if row['entry_floor'] and price<row['entry_floor']-1e-8:continue
                pnl=row['won']-price-fee(price)
                ruledef=registry[rule];m=markets[row['ticker']]
                entryrem=int((m['close']-row['ts'])/60)-1
                incomplete=False
                if ruledef.exit!='hold':
                    for exrem in range(entryrem,0,-1):
                        q=quote(m['br'].get(exrem))
                        if not q:continue
                        value=side_quote(q,row['side'])[0]
                        trigger=(ruledef.exit=='stop40' and value<=.4) or (ruledef.exit=='time3' and entryrem-exrem+1>=3)
                        if ruledef.exit in ('tp5_sl8','tp10_sl8'):
                            target=.05 if ruledef.exit=='tp5_sl8' else .10
                            trigger=value>=price+target or value<=price-.08
                        if not trigger:continue
                        exts=m['close']-exrem*60
                        j=bisect.bisect_left(times,exts+.3)
                        if j==len(bb) or bb[j]['ts']>exts+35:
                            incomplete=True;break
                        # Sell this outcome into its bids. Complement of the
                        # opposite buy ask supplies the same depth calculation.
                        opposite=executable_ask(bb[j],-row['side'])
                        if opposite is None:incomplete=True;break
                        exitprice=1-opposite[0]
                        pnl=exitprice-price-fee(price)-fee(exitprice)
                        break
                if incomplete:missing_exit+=1;continue
                valid.append(dict(row,entry=price,pnl=pnl));delays.append(bb[i]['ts']-row['ts'])
            result.append(dict(rule=rule,split=sp,candle_trades=len(chosen),snapshots_available=available,
                               over_limit=caps,missing_exit=missing_exit,complete_replays=len(valid),after_1c=metrics(valid),
                               adjusted_day_bootstrap=bootstrap(valid,[f'2026-09-{d:02}' for d in range(4,12)],.05/max(1,len(selected))) if sp=='test' else None,
                               mean_delay_s=sum(delays)/len(delays) if delays else None,
                               caveat='Paired candle-signal subset conditional on available recorded snapshots; prices and depth are observed, fills are not guaranteed.'))
    (ROOT/'book_audit.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
