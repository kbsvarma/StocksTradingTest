"""Pure tick-ladder rounding shared by execution and read-only diagnostics."""
from decimal import Decimal as D, ROUND_FLOOR, ROUND_CEILING
from lanes import lane_for

def limit_price(signal):
    """Round toward less spending on the market's YES-perspective tick ladder."""
    rule=lane_for(signal)
    cap=min(D(str(signal['signal_ask']))+D('.01'),rule.cap)
    yes=cap if signal['side']=='yes' else 1-cap
    ranges=signal.get('price_ranges')
    if not ranges:raise ValueError('market tick ladder unavailable')
    for r in ranges:
        lo,hi,step=D(r['start']),D(r['end']),D(r['step'])
        if step<=0:raise ValueError('invalid tick step')
        if lo<=yes<=hi:
            direction=ROUND_FLOOR if signal['side']=='yes' else ROUND_CEILING
            px=lo+((yes-lo)/step).to_integral_value(rounding=direction)*step
            if not lo<=px<=hi:continue
            spend=px if signal['side']=='yes' else 1-px
            if not rule.floor<=spend<=cap:raise ValueError('price cannot fit band and ticks')
            return px,spend
    raise ValueError('no valid price tick')

