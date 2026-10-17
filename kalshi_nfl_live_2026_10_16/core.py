"""Frozen NFL strategy, causal source selection, exact accounting and payloads."""
import datetime as dt,hashlib,json,math
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR
from zoneinfo import ZoneInfo
ET=ZoneInfo('America/New_York')
def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(x):return hashlib.sha256(canonical(x).encode()).hexdigest()
def ts(x):return dt.datetime.fromisoformat(x.replace('Z','+00:00')).timestamp()
def dec(x):
    y=D(str(x))
    if not y.is_finite():raise ValueError('Nonfinite number')
    return y
def fee(price,quantity=5,multiplier=1):
    return (D('.07')*dec(multiplier)*quantity*dec(price)*(1-dec(price))).quantize(D('.0001'),rounding=ROUND_CEILING)
def parse_plays(body):
    drives=body.get('drives') or {};current=drives.get('current') or {}
    # NFL live summaries can keep the unfinished drive outside `previous`.
    rows=[p for drive in drives.get('previous',[]) for p in drive.get('plays',[])]+current.get('plays',[])+(body.get('plays') or [])
    out={}
    for p in rows:
        try:
            clock=p['clock']['displayValue'].split(':');seconds=int(clock[0])*60+float(clock[1])
            r=dict(id=str(p['id']),seq=int(p['sequenceNumber']),wall=ts(p['wallclock']),period=int(p['period']['number']),clock=seconds,
                   home=int(p['homeScore']),away=int(p['awayScore']),text=p.get('text',''),modified=p.get('modified'))
            if r['home']<0 or r['away']<0 or not 0<=seconds<=900:raise ValueError('bad game state')
            old=out.get(r['id'])
            if old and any(old[k]!=r[k] for k in ('wall','seq','period','clock','home','away')):raise RuntimeError('conflicting play states in one source payload')
            out[r['id']]=r
        except (KeyError,ValueError,TypeError,IndexError):continue
    return sorted(out.values(),key=lambda x:(x['wall'],x['seq']))
def identity(body,game):
    comp=body.get('header',{}).get('competitions',[{}])[0]
    canon=lambda x:{'JAC':'JAX','WSH':'WAS','LA':'LAR'}.get(x,x)
    got={r['homeAway']:canon(r['team']['abbreviation']) for r in comp.get('competitors',[])}
    if str(body.get('header',{}).get('id'))!=str(game['espn_id']) or got!=game['teams']:return False
    return str(body.get('header',{}).get('season',{}).get('type')) in ('2','3')
def state_at(versions,boundary,cfg):
    # Only versions received by the boundary may affect that historical decision.
    byid={}
    for r in sorted(versions,key=lambda x:(x['received'],x['version'])):
        if r['received']<=boundary:byid[r['id']]=r
    candidates=[r for r in byid.values() if boundary-cfg['maximum_score_age_seconds']<=r['wall']<=boundary-cfg['source_lag_seconds']]
    if not candidates:return None
    return max(candidates,key=lambda x:(x['seq'],x['wall']))
def leader(state):
    if not state or state['period']!=4 or not 0<state['clock']<=300:return None
    return 'home' if state['home']>state['away'] else 'away' if state['away']>state['home'] else None
def current_leader(versions,now):
    valid=[p for p in versions if p['received']<=now and 0<=now-p['wall']<=120]
    return leader(max(valid,key=lambda p:(p['seq'],p['wall']))) if valid else None
def quote(c,side='yes'):
    try:b,a=dec(c['yes_bid']['close_dollars']),dec(c['yes_ask']['close_dollars'])
    except (KeyError,TypeError,ValueError,ArithmeticError):return None
    if not 0<=b<=a<=1:return None
    return (b,a) if side=='yes' else (1-a,1-b)
def choose(game,markets,candles,state,boundary):
    ahead=leader(state)
    if not ahead:return None,'score_or_clock_gate'
    target=game['teams'][ahead];choices=[]
    canon=lambda x:{'JAC':'JAX','WSH':'WAS','LA':'LAR'}.get(x,x)
    if len(markets)!=2 or {canon(m['ticker'].rsplit('-',1)[1]) for m in markets}!=set(game['teams'].values()):return None,'outcome_mapping'
    for m in markets:
        rule=m.get('rules_secondary','').lower()
        if 'tie' not in rule or not any(v in rule for v in ('$0.50','50/50')):return None,'noncomplementary_rules'
        if m.get('exchange_index')!=0 or m.get('status')!='active':continue
        if not ts(m['open_time'])<=boundary<ts(m['close_time']):continue
        side='yes' if canon(m['ticker'].rsplit('-',1)[1])==target else 'no'
        cs=candles.get(m['ticker'],{});q=quote(cs.get(boundary),side)
        if not q:continue
        bid,ask=q
        if not D('.90')<=ask<D('.95') or ask-bid>D('.02'):continue
        if sum(dec(cs.get(boundary-i*60,{}).get('volume_fp') or '0') for i in range(10))<5:continue
        cap=min(D('.98'),ask+D('.01'));ranges=m.get('price_ranges')
        if not ranges:return None,'tick_ladder_missing'
        # V2 uses YES-coordinate prices; round toward less spending.
        yes=cap if side=='yes' else 1-cap
        for r in ranges:
            lo,hi,step=map(dec,(r['start'],r['end'],r['step']))
            if lo<=yes<=hi and step>0:
                yes=lo+((yes-lo)/step).to_integral_value(rounding=ROUND_FLOOR if side=='yes' else ROUND_CEILING)*step;break
        else:continue
        cap=yes if side=='yes' else 1-yes
        if cap<ask:continue
        choices.append(dict(event=game['event'],ticker=m['ticker'],side=side,team=target,ask=str(ask),bid=str(bid),cap=str(cap),yes_limit=str(yes),boundary=boundary,state=state,quantity=5,exchange_index=0,subaccount=0))
    if not choices:return None,'price_spread_volume_gate'
    return min(choices,key=lambda x:(dec(x['ask']),x['ticker'],x['side'])),None
def depth(book,side,cap,quantity=5,min_price=D('.90')):
    if not book:return None,'book_unavailable'
    own='yes' if side=='yes' else 'no';opp='no' if side=='yes' else 'yes'
    try:
        bids=[(dec(p),dec(n)) for p,n in book[own+'_dollars'] if dec(n)>0]
        asks=sorted((1-dec(p),dec(n)) for p,n in book[opp+'_dollars'] if dec(n)>0)
        if not asks or not bids:return None,'one_sided_book'
        if any(not 0<=p<=1 or n<0 for p,n in bids+asks):return None,'invalid_book'
        if asks[0][0]<max(p for p,n in bids):return None,'crossed_book'
        # Frozen decision quote gates remain unchanged; do not chase a collapsed leader.
        if asks[0][0]<min_price:return None,'book_below_entry_floor'
        left=dec(quantity);cost=D(0);worst=None
        for price,n in asks:
            if price>dec(cap):break
            take=min(n,left);cost+=take*price;left-=take;worst=price
            if left==0:break
        if left:return None,'insufficient_depth_inside_cap'
        return dict(vwap=str(cost/quantity),cost=str(cost),worst=str(worst),quantity=quantity),None
    except (KeyError,ValueError,TypeError,ArithmeticError):return None,'invalid_book'
def risk_check(rows,cfg,now,required):
    openrows=[r for r in rows if r['status'] in ('pending','filled','unknown')]
    risk=sum(dec(r['reserve']) for r in openrows);contracts=sum(dec(r['quantity']) for r in openrows)
    realized=sum(dec(r['net']) for r in rows if r['status']=='settled')
    today=dt.datetime.fromtimestamp(now,ET).date()
    daily=sum(dec(r['net']) for r in rows if r['status']=='settled' and dt.datetime.fromtimestamp(r['settled'],ET).date()==today)
    cash=dec(cfg['bankroll'])+realized-risk
    if any(r['status']=='unknown' for r in rows):return 'unresolved_order'
    if contracts+5>cfg['max_contracts']:return 'contract_limit'
    if risk+required>dec(cfg['max_exposure']):return 'exposure_limit'
    if daily-risk-required < -dec(cfg['daily_loss']):return 'daily_loss_limit'
    if cash-required<dec(cfg['minimum_cash']):return 'bankroll_cash'
    return None
def order_payload(pick,client_id):
    if pick['quantity']!=5 or not pick['ticker'].startswith('KXNFLGAME-') or pick['exchange_index']!=0 or pick['subaccount']!=0:raise ValueError('Invalid NFL order route/size')
    return dict(ticker=pick['ticker'],client_order_id=client_id,side='bid' if pick['side']=='yes' else 'ask',count='5.00',price=f"{dec(pick['yes_limit']):.4f}",
                time_in_force='immediate_or_cancel',self_trade_prevention_type='taker_at_cross',post_only=False,reduce_only=False,cancel_order_on_pause=True,subaccount=0,exchange_index=0)
def percentile(values,p):
    a=sorted(values);return a[min(len(a)-1,math.ceil(len(a)*p)-1)] if a else None
