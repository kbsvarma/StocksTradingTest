"""Frozen fresh-impulse candidate. Pure calculation; cannot submit orders."""
import math,statistics

SPEC=dict(status='RESEARCH_ONLY',assets=['BTC','ETH'],contracts=5,minutes=[5,7],z_min=1.25,
          impulse_sigma_min=.25,signal_floor=.85,signal_ceiling=.95,buy_cap=.939,
          spread_max=.02,shared_contracts=20,shared_reserve=19.,shared_daily_budget=10.,
          one_entry_per_market=True,hold_to_settlement=True)

def decision(asset,T,start,close,current_quote,previous_quote,bars):
    """Quotes are completed-minute YES bid/ask; bar keys are minute START times.

    Require both observations to favor the same side and the last completed
    spot change to exceed 0.25 of the previous completed-hour log-return sigma.
    Never read a forming candle or use future settlement outcomes.
    """
    if asset not in SPEC['assets']:return dict(eligible=False,reason='asset')
    if T%60 or (close-T)%60:return dict(eligible=False,reason='minute_alignment')
    rem=(close-T)//60
    if not 5<=rem<=7:return dict(eligible=False,reason='time')
    try:
        bid,ask=map(float,current_quote);pb,pa=map(float,previous_quote)
        if not all(math.isfinite(x) for x in [bid,ask,pb,pa]) or not (0<=bid<=ask<=1 and 0<=pb<=pa<=1):raise ValueError()
    except (TypeError,ValueError):return dict(eligible=False,reason='quotes')
    side=1 if bid+ask>1 else -1;pside=1 if pb+pa>1 else -1
    if side!=pside:return dict(eligible=False,reason='direction_changed')
    bid,ask=(bid,ask) if side==1 else (1-ask,1-bid)
    if not .85-1e-8<=ask<.95-1e-8 or ask-bid>.02000001:return dict(eligible=False,reason='price_or_spread')
    try:
        values=[float(bars[t]) for t in range(T-62*60,T,60)];ref=float(bars[start-60])
        if any(not math.isfinite(x) or x<=0 for x in values+[ref]):raise ValueError()
    except (KeyError,TypeError,ValueError):return dict(eligible=False,reason='spot_gap')
    returns=[math.log(b/a) for a,b in zip(values,values[1:])]
    previous_sigma=statistics.pstdev(returns[:-1]);sigma=statistics.pstdev(returns[1:])
    if min(sigma,previous_sigma)<=0:return dict(eligible=False,reason='zero_sigma')
    z=side*(values[-1]/ref-1)/(sigma*math.sqrt(rem));impulse=side*returns[-1]/previous_sigma
    return dict(eligible=z>=1.25 and impulse>=.25,reason='eligible' if z>=1.25 and impulse>=.25 else 'cushion_or_impulse',
                z=z,impulse1=impulse,side=side,signal_ask=ask,remaining_minutes=rem,decision_ts=T)
