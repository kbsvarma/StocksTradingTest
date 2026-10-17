"""Pure prospective execution models. Never imports an exchange order client."""
import datetime as dt
import json
import math
import sys
from decimal import Decimal as D, ROUND_FLOOR, ROUND_CEILING
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parent/'frozen_source'))
from lanes import lane_for, ACTIVE_ASSETS, route_for
from price_rules import limit_price

def canonical(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), allow_nan=False)

def day(t):
    return dt.datetime.fromtimestamp(t, ZoneInfo('America/New_York')).date().isoformat()

def timestamp(s):
    return dt.datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()

def number(x):
    d = D(str(x))
    if not d.is_finite():
        raise ValueError('non-finite value')
    return d

def validate_signal(s):
    try:
        rule = lane_for(s)
        if s['asset'] not in ACTIVE_ASSETS or s['side'] not in ('yes', 'no'):
            return 'unsupported_asset_or_side'
        if s['exchange_index'] != route_for(s):
            return 'route_mismatch'
        t, close = number(s['decision_ts']), number(s['close_ts'])
        rem = number(s['remaining_minutes'])
        if close-t != rem*60 or not rule.min_minutes <= rem <= rule.max_minutes:
            return 'time_gate'
        bid, ask, z = (number(s[k]) for k in ('signal_bid','signal_ask','z'))
        if not 0 <= bid <= ask <= 1 or not rule.floor <= ask < rule.ceiling or ask-bid > D('.020000001'):
            return 'price_spread_gate'
        if float(z) < rule.threshold(float(rem)):
            return 'z_gate'
        if not s.get('signal_qualified'):
            return 'not_qualified_receipt'
        limit_price(s)
    except (KeyError, ValueError, TypeError, ArithmeticError):
        return 'incomplete_signal'
    return ''

def resting_price(s, discount):
    rule = lane_for(s)
    desired = min(number(s['signal_ask'])-number(discount), limit_price(s)[1])
    # Round the YES-perspective ladder toward less spending on either side.
    yes = desired if s['side']=='yes' else 1-desired
    for r in s['price_ranges']:
        lo, hi, step = (number(r[k]) for k in ('start','end','step'))
        if step <= 0:
            raise ValueError('invalid tick')
        if lo <= yes <= hi:
            rounding = ROUND_FLOOR if s['side']=='yes' else ROUND_CEILING
            p = lo+((yes-lo)/step).to_integral_value(rounding=rounding)*step
            spend = p if s['side']=='yes' else 1-p
            if lo <= p <= hi and rule.floor <= spend <= desired:
                return spend
    raise ValueError('discount falls below lane floor or invalid tick')

def book(payload, side):
    ob = payload['orderbook_fp']
    own = [(number(p), number(q)) for p,q in ob[side+'_dollars']]
    opposite = 'no' if side=='yes' else 'yes'
    asks = sorted((1-number(p), number(q)) for p,q in ob[opposite+'_dollars'])
    if any(not 0<=p<=1 or q<0 for p,q in own+asks):
        raise ValueError('invalid book level')
    own = [(p,q) for p,q in own if q>0]
    asks = [(p,q) for p,q in asks if q>0]
    bestbid = max((p for p,q in own), default=D(0))
    bestask = asks[0][0] if asks else None
    if bestask is not None and bestbid > bestask:
        raise ValueError('crossed book')
    return own, asks, bestask

def ioc_depth(payload, s):
    _, asks, best = book(payload, s['side'])
    if best is None or best < lane_for(s).floor:
        return None
    cap = limit_price(s)[1]
    need, levels = D(5), []
    for price, qty in asks:
        if price > cap:
            break
        q = min(need, qty)
        levels.append([str(price),str(q)])
        need -= q
        if need==0:
            return levels
    return None

def queue_ahead(own, price, depth_limit=100):
    if len(own)>=depth_limit and price<min(p for p,q in own):
        raise ValueError('queue_depth_truncated')
    return sum((q for p,q in own if p==price), D(0))

def fee(levels, multiplier, maker=False, fee_type='quadratic'):
    # Order-level rounding, one fixed price for resting orders; no invented rebate.
    coefficient = D(0) if maker and fee_type=='quadratic' else D('.0175') if maker else D('.07')
    model = sum(coefficient*number(multiplier)*number(q)*number(p)*(1-number(p)) for p,q in levels)
    cost = sum(number(p)*number(q) for p,q in levels)
    tradefee = model.quantize(D('.000001'), rounding=ROUND_CEILING)
    return (cost+tradefee).quantize(D('.0001'), rounding=ROUND_CEILING)-cost

def apply_trade(order, trade):
    """Queue model: cancellations never advance us; a quote touch never fills us.

    A sell through our bid credits only observed aggressive quantity. At the bid,
    subtract the entire displayed queue captured at submission first. Public
    prints do not prove our hypothetical queue position or a real fill.
    """
    if trade.get('is_block_trade') is not False or trade.get('ticker') != order['ticker']:
        return D(0), 'block_or_wrong_market'
    try:
        t = timestamp(trade['created_time'])
        px, qty = number(trade[order['side']+'_price_dollars']), number(trade['count_fp'])
        yes, no = number(trade['yes_price_dollars']), number(trade['no_price_dollars'])
        if yes+no != 1 or qty<=0 or not 0<px<1:
            return D(0), 'invalid_trade'
        aggressor = trade.get('taker_side')
        if aggressor not in ('yes','no'):
            return D(0), 'unknown_aggressor'
        # Legacy taker_side is the purchased outcome; opposite means sell our outcome.
        if aggressor == order['side']:
            return D(0), 'wrong_direction'
        if not order['accepted_ts'] <= t < order['effective_expiry']:
            return D(0), 'outside_order_lifetime'
        bid = number(order['price'])
        if px > bid:
            return D(0), 'above_bid'
        if px == bid:
            queue = number(order['queue_remaining'])
            consumed = min(queue,qty)
            order['queue_remaining'] = str(queue-consumed)
            qty -= consumed
        q = min(D(5)-number(order['filled']), qty)
        if q<=0:
            return D(0), 'queue_ahead'
        order['filled'] = str(number(order['filled'])+q)
        return q, 'through_bid' if px<bid else 'queue_exhausted'
    except (KeyError, ValueError, TypeError, ArithmeticError):
        return D(0), 'invalid_trade'

def risk_reason(existing, s, now, cash, max_contracts=25, max_risk='25', daily_budget='20'):
    """Outstanding resting quantity is reserved, including not-yet-confirmed fills."""
    active = [r for r in existing if not r.get('settled') and (r['state'] in ('resting','waiting_control') or number(r.get('filled',0))>0)]
    if any(r['asset']==s['asset'] for r in active):
        return 'asset_exposure'
    quantities = [D(5) if r['state'] in ('resting','waiting_control') else number(r['filled']) for r in active]
    risk = sum(q*number(r['reserve']) for q,r in zip(quantities, active))
    newrisk = 5*lane_for(s).reserve
    if sum(quantities)+5>max_contracts:
        return 'contract_cap'
    if risk+newrisk>number(max_risk):
        return 'exposure_cap'
    pnl = sum(number(r.get('pnl',0)) for r in existing if r.get('settled') and day(r['settled_ts'])==day(now))
    if pnl-risk-newrisk < -number(daily_budget):
        return 'daily_loss_including_open_risk'
    route = route_for(s)
    spent = sum(number(r.get('cost',0))+number(r.get('fees',0)) for r in existing if r.get('route')==route)
    payouts = sum(number(r.get('payout',0)) for r in existing if r.get('route')==route)
    resting = sum((D(5)-number(r.get('filled',0)))*number(r['reserve']) for r in active if r.get('route')==route and r['state'] in ('resting','waiting_control'))
    if number(cash[str(route)])-spent+payouts-resting-newrisk < 2:
        return 'route_cash'
    return ''
