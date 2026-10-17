import json,math,statistics,collections,datetime,re
from decimal import Decimal,ROUND_FLOOR,ROUND_CEILING
from pathlib import Path
P=Path(__file__).resolve().parent;Q=P.parent/'kalshi_full_review_2026_09_26/candidate_data';START=1788825600;END=1790380800;SPLIT=(START+END)//2
ASSETS=['DOGE','BNB','NEAR','ZEC','HYPE']
def ts(s):return int(datetime.datetime.fromisoformat(re.sub(r'\.(\d+)',lambda m:'.'+m[1][:6].ljust(6,'0'),s).replace('Z','+00:00')).timestamp())
def sideq(c,side,phase):
 try:b,a=float(c['b'+phase]),float(c['a'+phase])
 except (KeyError,TypeError,ValueError):return None
 if not 0<=b<=a<=1:return None
 return (b,a) if side=='yes' else (1-a,1-b)
def cap(ask,side,ladder):
 d=Decimal;v=min(d(str(ask))+d('.01'),d('.939'));y=v if side=='yes' else 1-v
 for rg in ladder:
  lo,hi,step=map(d,(rg['start'],rg['end'],rg['step']))
  if lo<=y<=hi:
   px=lo+((y-lo)/step).to_integral_value(rounding=ROUND_FLOOR if side=='yes' else ROUND_CEILING)*step;s=px if side=='yes' else 1-px
   if lo<=px<=hi and d('.85')<=s<=v:return float(s)
 return None
out=[];validation=[]
for asset in ASSETS:
 markets=json.loads((Q/f'KX{asset}15M_markets.json').read_text());markets=[m for m in markets if START<=ts(m['close_time'])<END];quotes={r['ticker']:{c['t']:c for c in r['candles']} for r in map(json.loads,(Q/f'KX{asset}15M_candles.jsonl').read_text().splitlines())};prices={int(x[0]):float(x[4]) for b in map(json.loads,(P/'data'/f'spot_{asset}.jsonl').read_text().splitlines()) for x in b['rows']}
 for phase in ['o','c']:
  counters=collections.Counter();rows=[]
  for m in sorted(markets,key=lambda m:m['close_time']):
   cl,op=ts(m['close_time']),ts(m['open_time']);cs=quotes.get(m['ticker'],{})
   for rem in range(10,2,-1):
    T=cl-rem*60;counters['decisions']+=1;y=sideq(cs.get(T,{}),'yes','c')
    if not y:counters['missing_quote']+=1;continue
    side='yes' if sum(y)>1 else 'no';bid,ask=sideq(cs[T],side,'c')
    if not .85-1e-8<=ask<.93-1e-8 or ask-bid>.02000001:counters['price_spread_reject']+=1;continue
    counters['in_band']+=1;keys=list(range(T-3660,T,60))
    if op-60 not in prices or not all(k in prices for k in keys):counters['spot_gap']+=1;continue
    vals=[prices[k] for k in keys];sigma=statistics.pstdev(math.log(b/a) for a,b in zip(vals,vals[1:]))
    if sigma<=0:counters['zero_sigma']+=1;continue
    z=(1 if side=='yes' else -1)*(prices[T-60]/prices[op-60]-1)/sigma/math.sqrt(rem)
    if z<2:counters['cushion_reject']+=1;continue
    counters['signal']+=1;nxt=sideq(cs.get(T+60,{}),side,phase);limit=cap(ask,side,m.get('price_ranges',[]))
    if not nxt or limit is None:counters['missing_next_or_tick']+=1;continue
    price=nxt[1]
    if not .85-1e-8<=price<=limit+1e-8:counters['outside_cap']+=1;continue
    win=m['result']==side;fee=math.ceil((.07*2*price*(1-price)-1e-12)*10000)/10000;net=2*(int(win)-price)-fee
    row={'asset':asset,'ticker':m['ticker'],'signal_ts':T,'entry_ts':T+(1 if phase=='o' else 60),'close_ts':cl,'settled_ts':ts(m.get('settlement_ts') or m['close_time']),'side':side,'z':z,'sigma':sigma,'spot':prices[T-60],'strike_proxy':prices[op-60],'ask':ask,'cap':limit,'entry':price,'win':win,'fee':fee,'net':net,'stress1':net-.02,'stress2':net-.04};rows.append(row);break
  eq=peak=dd=0
  for row in rows:eq+=row['stress1'];peak=max(peak,eq);dd=max(dd,peak-eq)
  stats={'asset':asset,'execution':'next_open' if phase=='o' else 'next_close','markets':len(markets),'missing_minutes':sum(t not in prices for t in range(START,END,60)),'expected_minutes':(END-START)//60,'trades':len(rows),'wins':sum(r['win'] for r in rows),'losses':sum(not r['win'] for r in rows),'net':sum(r['net'] for r in rows),'stress1':sum(r['stress1'] for r in rows),'stress2':sum(r['stress2'] for r in rows),'first_half':sum(r['stress1'] for r in rows if r['close_ts']<SPLIT),'second_half':sum(r['stress1'] for r in rows if r['close_ts']>=SPLIT),'max_drawdown':dd,'filters':dict(counters)}
  (P/f'{asset}_{phase}_trades.json').write_text(json.dumps(rows,indent=2));out.append(stats);print(json.dumps(stats),flush=True)
  for r in rows:
   assert r['entry_ts']<r['close_ts'] and r['z']>=2 and r['entry']<=r['cap']+1e-8
   assert abs(r['net']-(2*(int(r['win'])-r['entry'])-r['fee']))<1e-9
(P/'results.json').write_text(json.dumps(out,indent=2))
