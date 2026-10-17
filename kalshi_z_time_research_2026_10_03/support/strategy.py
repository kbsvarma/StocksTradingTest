"""Isolated research rules. No account, order client, or trading methods."""
from dataclasses import dataclass, asdict
from decimal import Decimal as D, ROUND_FLOOR, ROUND_CEILING
import math

@dataclass(frozen=True)
class Rule:
    name: str
    lo: float
    hi: float
    zmin: float
    remlo: int
    remhi: int
    hardcap: float
    kind: str = 'cushion'
    edge: float = 0

RULES = [
    Rule('current_signal95', .85, .95, 2, 3, 10, .939),
    Rule('cheaper_z2', .65, .95, 2, 3, 10, .939),
    Rule('early_z2', .70, .95, 2, 11, 13, .939),
    Rule('late_z2', .85, .975, 2, 1, 2, .975),
    Rule('late_z3', .90, .985, 3, 1, 2, .980),
    Rule('premium_z25', .93, .98, 2.5, 3, 10, .975),
    Rule('premium_z3', .93, .98, 3, 3, 10, .975),
    Rule('broad_z2', .70, .98, 2, 1, 13, .975),
    Rule('gaussian_edge5', .55, .95, 1.5, 3, 10, .939, 'gaussian', .05),
    Rule('gaussian_edge8', .55, .95, 1.5, 3, 10, .939, 'gaussian', .08),
    Rule('empirical_edge3', .55, .98, 0, 3, 10, .975, 'empirical', .03),
    Rule('empirical_edge5', .55, .98, 0, 3, 10, .975, 'empirical', .05),
]

def fee(price, quantity=1, precision=.0001):
    cost = D(str(price))*D(str(quantity))
    raw = D('.07')*cost*(1-D(str(price)))
    debit = ((cost+raw)/D(str(precision))).to_integral_value(rounding=ROUND_CEILING)*D(str(precision))
    return float(debit-cost)

def probability(f, model):
    x = model_features(f)
    v = max(-30, min(30, sum(a*b for a,b in zip(x, model))))
    return 1/(1+math.exp(-v))

def model_features(f):
    mid = min(.999, max(.001, (f['bid']+f['ask'])/2))
    return [1., math.log(mid/(1-mid)), max(-5.,min(5.,f['z'])), math.log(f['rem']/7)]

def qualifies(r, f, model=None):
    if not r.remlo <= f['rem'] <= r.remhi: return False
    if not r.lo-1e-8 <= f['ask'] < r.hi-1e-8: return False
    if f['ask']-f['bid'] > .02000001 or f['z'] < r.zmin: return False
    if r.kind != 'cushion':
        p = .5*(1+math.erf(f['z']/math.sqrt(2))) if r.kind == 'gaussian' else probability(f,model)
        if p-f['ask']-fee(f['ask']) < r.edge: return False
    return True

def tick_cap(r, f, ranges):
    """Round on YES ladder toward cheaper purchase for either outcome."""
    cap = min(D(str(r.hardcap)), D(str(f['ask']))+D('.01'))
    target = cap if f['side']==1 else 1-cap
    candidates=[]
    for band in ranges:
        lo,hi,step=map(D,[band['start'],band['end'],band['step']])
        rounding=ROUND_FLOOR if f['side']==1 else ROUND_CEILING
        point=lo+((target-lo)/step).to_integral_value(rounding=rounding)*step
        point=max(lo,min(hi,point))
        price=point if f['side']==1 else 1-point
        if price<=cap:candidates.append(price)
    return float(max(candidates)) if candidates else None

def book_fill(book, side, cap, floor, quantity=1):
    """book=(timestamp, yes bids, no bids); full displayed depth required."""
    levels=book[2] if side==1 else book[1]
    remaining=float(quantity);cost=0;rawfee=0
    for bid,n in sorted(levels,reverse=True):
        price=1-bid
        if n<=0:continue
        if not 0<=price<=1:continue
        if price<floor-1e-8:return None
        if price>cap+1e-8:break
        take=min(n,remaining);remaining-=take
        cost+=take*price;rawfee+=.07*take*price*(1-price)
        if remaining<=1e-8:
            debit=math.ceil((cost+rawfee-1e-10)*10000)/10000
            return cost/quantity, debit-cost
    return None

def definitions():return [asdict(r) for r in RULES]
