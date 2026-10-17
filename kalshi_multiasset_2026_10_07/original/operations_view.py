"""Read-only, unique-market funnel. Rechecks never inflate opportunity counts."""
import json
import sqlite3
from lanes import LANES

def funnel(events,rows):
    orders={r['ticker']:r for r in rows}
    out=[]
    for lane,rule in LANES.items():
        groups={};owned=set();errors=set()
        for r in events:
            if r.get('kind') or r.get('lane','cushion_z2')!=lane:continue
            t=r.get('ticker')
            if r.get('status')=='data_error':errors.add((r.get('asset'),r.get('decision_ts')))
            if r.get('status') in ('already_entered','position_filled','position_settled','zero_fill_locked','order_pending','order_rejected'):
                if t:owned.add(t)
                continue
            if not t or not isinstance(r.get('z'),(int,float)) or r['z']<rule.z:continue
            if not (float(rule.floor)-1e-8<=r.get('signal_ask',0)<float(rule.ceiling)-1e-8):continue
            groups.setdefault(t,[]).append(r)
        counts=dict(qualified=len(groups),filled=0,other_lane=0,zero_fill=0,pending=0,risk_blocked=0,book_blocked=0,other=0)
        for ticker,observations in groups.items():
            order=orders.get(ticker,{})
            if float(order.get('filled',0))>0:
                key='filled' if order.get('lane','cushion_z2')==lane else 'other_lane'
            elif order.get('status') in ('intent','unknown','ack','accounting_pending'):key='pending'
            elif order.get('status')=='unfilled':key='zero_fill'
            elif any(any(x in r.get('execution_status','').lower() for x in ('risk','budget','cap','cash','exposure')) and 'depth' not in r.get('execution_status','').lower() for r in observations):key='risk_blocked'
            elif any(r.get('status') in ('book_outside_limit','insufficient_depth') or r.get('execution_status') in ('Insufficient depth inside cap','Book weakened below floor') for r in observations):key='book_blocked'
            else:key='other'
            counts[key]+=1
        out.append(dict(lane=lane,**counts,owned_market_skips=len(owned),data_error_minutes=len(errors)))
    return out

def read_funnel(path,since):
    c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)
    try:
        events=[json.loads(r[0]) for r in c.execute('SELECT event FROM events WHERE ts>=?',(since,))]
        rows=[json.loads(r[0]) for r in c.execute('SELECT record FROM orders')]
        return funnel(events,rows)
    finally:c.close()
