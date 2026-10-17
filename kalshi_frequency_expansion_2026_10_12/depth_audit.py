"""Read-only match of frozen new hypotheses to already recorded production books."""
from core import *
import sqlite3
import argparse
def main(conditional=False):
    prefix='conditional_' if conditional else ''
    records=read(P/('conditional_evaluate_ledgers.json.gz' if conditional else 'financial_evaluate_ledgers.json.gz'))['q5' if conditional else 'floor0.5_q5']['base']['nonmetal']['continuous']
    floor=.1 if conditional else .5
    keys={(r['ticker'],r['t']) for r in records};side_lookup={};samples=C.defaultdict(list);events=[]
    L=ROOT/'kalshi_revalidation_2026_09_12/live_v1'
    src=sqlite3.connect((L/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True)
    for raw, in src.execute('SELECT event FROM events WHERE ts>=? AND ts<?',(min(r['t'] for r in records),max(r['t'] for r in records)+60)):
        e=json.loads(raw);key=e.get('ticker'),e.get('decision_ts')
        if key not in keys or e.get('side') not in ('yes','no'):continue
        side=1 if e['side']=='yes' else -1;side_lookup[key+(e.get('lane'),)]=side
        for tkey,lkey in (('book_observed_ts','book_levels'),('execution_book_ts','execution_book_levels')):
            if tkey in e and lkey in e and 0<=e[tkey]-key[1]<=25:
                samples[key+(side,)].append(dict(t=e[tkey],levels=e[lkey],source=tkey))
        events.append({k:e[k] for k in ('ticker','decision_ts','lane','side','book_observed_ts','book_levels','execution_book_ts','execution_book_levels') if k in e})
    src.close();observer=[]
    with (ROOT/'kalshi_execution_retry_2026_10_02/observer_state/observations.jsonl').open() as f:
        for line in f:
            try:e=json.loads(line)
            except json.JSONDecodeError:continue # a currently appending final line is not evidence
            key=e.get('ticker'),e.get('decision_ts');side=side_lookup.get(key+(e.get('lane'),))
            if key in keys and side and e.get('levels') is not None and 0<=e['observed_ts']-key[1]<=25:
                samples[key+(side,)].append(dict(t=e['observed_ts'],levels=e['levels'],source='observer'));observer.append(e)
    save(prefix+'depth_events.json.gz',events);save(prefix+'depth_observer.json.gz',observer)
    checked=[];overlay=[]
    for r in records:
        ss=samples.get((r['ticker'],r['t'],r['side']),[]);ss=sorted({(s['t'],json.dumps(s['levels'])):s for s in ss}.values(),key=lambda s:s['t'])
        status='unknown' if not ss else 'sampled_no_fill';fill=None
        for sample in ss:
            q=cost=0.;below=False
            for px,n in sorted((1-float(px),float(n)) for px,n in sample['levels']):
                if n<=0:continue
                if px<floor-1e-8:below=True
                if px>r['limit']+1e-8:break
                take=min(5-q,n);cost+=take*px;q+=take
                if q>=5-1e-8:break
            if q>=5-1e-8 and not below:
                status='supported';fill=dict(price=cost/5,t=sample['t']);break
        checked.append(dict(ticker=r['ticker'],asset=r['asset'],t=r['t'],side=r['side'],status=status,observations=len(ss),
                            quote_entry=r['entry'],quote_net=r['net'],fill=fill))
        if status=='sampled_no_fill':continue
        rr=dict(r)
        if fill:rr.update(entry=max(r['signal_ask'],fill['price']),entry_t=fill['t'])
        overlay.append(rr)
    ledger,account=portfolio(overlay,5,.01);m=metrics(ledger,28);m.update(account)
    save(prefix+'depth_audit.json',dict(counts=C.Counter(r['status'] for r in checked),rows=checked,overlay=m,
        known_quotes_net={s:sum(r['quote_net'] for r in checked if r['status']==s) for s in ('supported','sampled_no_fill','unknown')},
        note='Historical sampled depth is not actual fills; observer is selective for incumbent bot signals. Unknown quotes remain in overlay and must NOT be called depth-verified. No production or observer changes.'))
    save(prefix+'depth_overlay_ledgers.json.gz',ledger)
    print('DEPTH',C.Counter(r['status'] for r in checked),'overlay',m['trades'],m['net'],flush=True)
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--conditional',action='store_true');args=parser.parse_args();main(args.conditional)
