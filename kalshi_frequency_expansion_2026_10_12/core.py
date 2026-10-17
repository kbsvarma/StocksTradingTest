"""Offline shared-bank accounting. No network or order interface."""
import collections as C
import datetime as dt
from decimal import Decimal as D,ROUND_CEILING
import gzip
import hashlib
import heapq
import json
from pathlib import Path
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent
ROOT=P.parent
ET=ZoneInfo('America/New_York')
def read(p):
    p=Path(p)
    return json.load(gzip.open(p,'rt')) if p.suffix=='.gz' else json.loads(p.read_text())
def save(n,x):
    p=P/n;p.parent.mkdir(parents=True,exist_ok=True)
    if p.suffix=='.gz':
        with gzip.open(p,'wt') as f:json.dump(x,f,separators=(',',':'),allow_nan=False)
    else:p.write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def ts(s):return int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
def day(t):return dt.datetime.fromtimestamp(t,ET).date().isoformat()
def fee(px,q=1,mult=1):
    p=D(str(px));gross=p*q
    raw=(D('.07')*D(str(mult))*q*p*(1-p)).quantize(D('.000001'),rounding=ROUND_CEILING)
    return float((gross+raw).quantize(D('.0001'),rounding=ROUND_CEILING)-gross)
def quote(c,phase='close',side=1):
    if c is None:return None
    try:
        if 'bc' in c:b,a=float(c['b'+phase[0]]),float(c['a'+phase[0]])
        else:
            out=[]
            for k in ('yes_bid','yes_ask'):
                v=c[k].get(phase+'_dollars',c[k].get(phase));out.append(float(v)/100 if isinstance(v,int) else float(v))
            b,a=out
    except (KeyError,TypeError,ValueError):return None
    if not 0<=b<=a<=1:return None
    return (round(b,8),round(a,8)) if side==1 else (round(1-a,8),round(1-b,8))
def metrics(rows,days):
    realized=C.defaultdict(float);daily=C.defaultdict(float);counts=C.Counter();assets=C.defaultdict(list)
    for r in rows:
        realized[r['exit_t']]+=r['net'];daily[day(r['entry_t'])]+=r['net'];counts[day(r['entry_t'])]+=1;assets[r['asset']].append(r)
    eq=peak=dd=0
    for t in sorted(realized):eq+=realized[t];peak=max(peak,eq);dd=max(dd,peak-eq)
    return dict(trades=len(rows),positive=sum(r['net']>1e-8 for r in rows),negative=sum(r['net']<-1e-8 for r in rows),
        net=round(eq,6),realized_drawdown=round(dd,6),fees=round(sum(r['fees'] for r in rows),6),
        friction=round(sum(r['friction_total'] for r in rows),6),calendar_days=days,trades_per_day=len(rows)/days,
        active_days=len(counts),daily_counts=dict(sorted(counts.items())),daily_net={k:round(v,6) for k,v in sorted(daily.items())},
        without_best3=round(eq-sum(sorted((r['net'] for r in rows),reverse=True)[:3]),6),
        assets={a:dict(trades=len(rs),net=round(sum(r['net'] for r in rs),6),positive=sum(r['net']>0 for r in rs)) for a,rs in sorted(assets.items())})
def portfolio(pool,qty=1,friction=.01,sports=False):
    cash=100.;active={};queue=[];serial=0;closed=[];seen=set();last=C.defaultdict(lambda:-float('inf'));counts=C.Counter();daily=C.defaultdict(float);blocks=C.Counter();mincash=cash;peakreserved=0
    def settle(t):
        nonlocal cash
        while queue and queue[0][0]<=t:
            _,i=heapq.heappop(queue);r=active.pop(i)
            cash+=qty*r['exit_price']-r['exit_fee']-r['exit_friction'];daily[day(r['exit_t'])]+=r['net'];closed.append(r)
            last[r['owner']]=r['exit_t']
    for r in sorted(pool,key=lambda x:(x['entry_t'],x['asset'],x['owner'],x['ticker'],x['side'])):
        settle(r['entry_t'])
        if not sports and r['owner'] in seen:continue
        if any(a['owner']==r['owner'] for a in active.values()):blocks['owner_active']+=1;continue
        if sports and (counts[r['owner']]>=4 or r['entry_t']<last[r['owner']]+300):blocks['cooldown_or_four_cycles']+=1;continue
        f0=fee(r['entry'],qty,r['mult']);f1=0 if r['exit_kind']=='settlement' else fee(r['exit_price'],qty,r['mult'])
        ef=0 if r['exit_kind']=='settlement' else qty*friction
        cost=qty*(r['entry']+friction)+f0
        reserve=cost+(qty*(friction+.0175*r['mult']) if sports else 0)
        reserved=sum(a['reserve'] for a in active.values())+reserve
        reason=None
        if len(active)*qty+qty>25:reason='contract_limit'
        elif reserved>25+1e-8:reason='reserved_exposure'
        elif daily[day(r['entry_t'])]-reserved < -20-1e-8:reason='daily_including_open_risk'
        elif cash-cost<2-1e-8:reason='cash'
        if reason:blocks[reason]+=1;continue
        net=qty*(r['exit_price']-r['entry'])-f0-f1-qty*friction-ef
        a=dict(r,qty=qty,entry_fee=f0,exit_fee=f1,exit_friction=ef,fees=f0+f1,
            friction_total=qty*friction+ef,net=net,reserve=reserve)
        cash-=cost;mincash=min(mincash,cash);peakreserved=max(peakreserved,reserved)
        active[serial]=a;heapq.heappush(queue,(r['exit_t'],serial));serial+=1;seen.add(r['owner']);counts[r['owner']]+=1
    settle(float('inf'))
    assert abs(cash-100-sum(r['net'] for r in closed))<1e-6
    return closed,dict(blocks=blocks,min_cash=mincash,peak_reserved=peakreserved,final_cash=cash)
