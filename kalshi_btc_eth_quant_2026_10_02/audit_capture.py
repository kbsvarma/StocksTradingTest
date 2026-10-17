"""Audit completed compressed capture segments; never assumes a paper fill."""
from pathlib import Path
from decimal import Decimal as D
import collections,gzip,json
import numpy as np
import forward_capture as capture

P=Path(__file__).resolve().parent
if __name__=='__main__':
    counts=collections.Counter();delays=collections.defaultdict(list);anomalies=collections.Counter();segments=[]
    for path in sorted((P/'forward_sample').glob('*.gz')):
        books={};seq={};first=None;last=None;complete=False
        with gzip.open(path,'rt') as inp:
            for line in inp:
                r=json.loads(line);kind=r.get('type','unknown');counts[kind]+=1;msg=r.get('msg',{})
                now=r['client_received_at_ms'];first=now if first is None else first;last=now
                if capture.gap(seq,r):anomalies['sequence_gap']+=1
                if kind=='capture_end':complete=r.get('complete_to_close',False)
                sent=r.get('sending_ts_ms')
                if sent is not None:delays['server_send_to_capture_ms'].append(now-sent)
                ticker=msg.get('market_ticker')
                if kind=='orderbook_snapshot':
                    books[ticker]={side:{D(p):D(q) for p,q in msg.get(side+'_dollars_fp',[]) if D(q)>0} for side in ('yes','no')}
                elif kind=='orderbook_delta':
                    if ticker not in books:anomalies['delta_before_snapshot']+=1;continue
                    side=msg['side'];price=D(msg['price_dollars']);qty=books[ticker][side].get(price,D(0))+D(msg['delta_fp'])
                    if qty<0:anomalies['negative_depth']+=1
                    if qty<=0:books[ticker][side].pop(price,None)
                    else:books[ticker][side][price]=qty
                elif kind.startswith('cfbenchmarks_value'):
                    if kind=='cfbenchmarks_value':
                        raw=json.loads(msg['data']);source=int(raw['time'])
                    elif kind=='cfbenchmarks_value_5hz':source=msg['source_ts_ms']
                    else:continue
                    delays[kind+'_source_to_capture_ms'].append(now-source)
        segments.append(dict(file=path.name,bytes=path.stat().st_size,start_ms=first,end_ms=last,complete_to_close=complete))
    out=dict(segments=segments,counts=dict(counts),anomalies=dict(anomalies),
        latency={k:dict(n=len(v),p50=float(np.percentile(v,50)),p95=float(np.percentile(v,95)),p99=float(np.percentile(v,99))) for k,v in delays.items()},
        note='Receive-time measurements from this server clock, not exchange matching latency or proof of order fills. Partial/reconnect segments remain explicitly marked.')
    (P/'capture_audit.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
