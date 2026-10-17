"""Frozen research-only ETH pullback signal. No account or order capability."""
import math

def signal(asset,previous_bid,previous_ask,bid,ask,minutes_remaining):
    if asset!='ETH' or minutes_remaining not in (6,7,8):return None
    values=(previous_bid,previous_ask,bid,ask)
    if any(v is None or not math.isfinite(v) for v in values):return None
    if not 0<=previous_bid<=previous_ask<=1 or not 0<=bid<=ask<=1:return None
    if ask-bid>.02000001:return None
    side=1 if bid+ask>1 else -1
    own_ask=ask if side==1 else 1-bid
    if not .55-1e-8<=own_ask<=.65+1e-8:return None
    change=side*((bid+ask-previous_bid-previous_ask)/2)
    if change>-.02+1e-8:return None
    return dict(asset=asset,side='yes' if side==1 else 'no',ask=own_ask,midpoint_change=change,minutes_remaining=minutes_remaining,minimum_buy_price=.55,maximum_buy_price=.65,exit='hold_to_settlement',status='RESEARCH_ONLY')

