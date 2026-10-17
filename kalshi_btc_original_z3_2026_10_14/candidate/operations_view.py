"""Read-only execution stages; count distinct markets, never polling attempts."""
import json,math,sqlite3
from lanes import LANES,lane_for,record_asset,Z2
from price_rules import limit_price

OWNED=('already_entered','position_filled','position_settled','zero_fill_locked','order_pending','order_rejected')
def qualified(r):
    if r.get('kind') or r.get('status') in OWNED or not r.get('ticker'):return False
    try:
        rule=lane_for(r);z=float(r['z']);m=float(r['remaining_minutes']);ask=float(r['signal_ask']);bid=float(r['signal_bid'])
        # Historical qualification uses the threshold captured at decision time.
        required=float(r.get('required_z',rule.threshold(m)))
        return (all(math.isfinite(x) for x in (z,m,ask,bid,required)) and required>0 and rule.min_minutes<=m<=rule.max_minutes and
                z>=required and float(rule.floor)-1e-8<=ask<float(rule.ceiling)-1e-8 and 0<=bid<=ask<=1 and ask-bid<=.02000001)
    except (ValueError,KeyError,TypeError):return False

def summary(events,rows,assets=None,lane=None):
    events=[r for r in events if (assets is None or record_asset(r) in assets) and (lane is None or r.get('lane',Z2)==lane)]
    rows=[r for r in rows if assets is None or record_asset(r) in assets]
    orders={r['ticker']:r for r in rows};groups={};owned=set();errors=set();gaps=set()
    for r in events:
        if r.get('kind'):continue
        key=(r.get('asset'),r.get('lane',Z2),r.get('decision_ts'))
        if r.get('status')=='data_error':errors.add(key)
        if r.get('status') in ('incomplete_spot','decision_candle_unavailable','no_decision_quote'):gaps.add(key)
        if r.get('status') in OWNED and r.get('ticker'):owned.add(r['ticker'])
        if qualified(r):groups.setdefault(r['ticker'],[]).append(r)
    out=dict(qualified=len(groups),executable=0,submitted=0,filled=0,other_lane=0,zero_fill=0,pending=0,risk_blocked=0,book_blocked=0,other=0,
             owned_market_skips=len(owned),data_error_minutes=len(errors),input_gap_minutes=len(gaps),historical_executable_lower_bound=0)
    details=[]
    for ticker,observations in groups.items():
        order=orders.get(ticker,{});own=bool(order) and (lane is None or order.get('lane',Z2)==lane)
        # Ledger submissions prove earlier risk/book checks passed. No inference
        # from the scanner's old 'eligible' label or a preliminary book alone.
        submitted=own and order.get('status') in ('unknown','ack','open','accounting_pending','settled','unfilled','rejected')
        explicit=any(r.get('execution_executable') is True and 0<=r.get('execution_verified_ts',-1)-r.get('decision_ts',0)<=35 for r in observations)
        executable=explicit or submitted
        out['submitted']+=int(submitted);out['executable']+=int(executable)
        out['historical_executable_lower_bound']+=int(submitted and not explicit)
        if float(order.get('filled',0))>0:key='filled' if own else 'other_lane'
        elif order.get('status') in ('intent','unknown','ack','accounting_pending'):key='pending'
        elif order.get('status')=='unfilled':key='zero_fill'
        elif any(r.get('status') in ('book_outside_limit','insufficient_depth') or r.get('execution_status') in ('Insufficient depth inside cap','Book weakened below floor') for r in observations):key='book_blocked'
        elif any(any(x in r.get('execution_status','').lower() for x in ('risk','budget','cash','exposure','contract cap')) for r in observations):key='risk_blocked'
        else:key='other'
        out[key]+=1
        last=max(observations,key=lambda r:r.get('_saved_ts',r.get('execution_book_ts',r.get('book_observed_ts',r.get('observed_ts',0)))))
        try:cap=str(limit_price(last)[1])
        except (KeyError,TypeError,ValueError):cap=None
        details.append(dict(ticker=ticker,asset=record_asset(last),lane=last.get('lane',Z2),decision_ts=last.get('decision_ts'),qualified=True,executable=bool(executable),submitted=bool(submitted),filled=key=='filled',outcome=key,
                            limit=last.get('execution_cap') or order.get('favorite_limit') or cap,reason=last.get('execution_status') or last.get('status'),historical_evidence=bool(submitted and not explicit)))
    return dict(counts=out,markets=sorted(details,key=lambda r:r.get('decision_ts') or 0,reverse=True))

def funnel(events,rows,assets=None):
    return [dict(lane=lane,**summary(events,rows,assets,lane)['counts']) for lane in LANES]

def read_operations(path,since,assets=None,lane=None):
    c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)
    try:
        events=[]
        for saved,raw in c.execute('SELECT ts,event FROM events WHERE ts>=?',(since,)):
            r=json.loads(raw);r['_saved_ts']=saved
            if r.get('decision_ts',saved)>=since:events.append(r)
        rows=[json.loads(r[0]) for r in c.execute('SELECT record FROM orders')]
        result=summary(events,rows,assets,lane)
        result['lanes']=[r for r in funnel(events,rows,assets) if lane is None or r['lane']==lane]
        return result
    finally:c.close()

def read_funnel(path,since,assets=None):return read_operations(path,since,assets)['lanes']
