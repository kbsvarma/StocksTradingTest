"""Quote-only diagnostic. Cannot evaluate z without underlying Pyth data."""
import json,math,statistics,collections
from pathlib import Path
from datetime import datetime
from decimal import Decimal,ROUND_FLOOR,ROUND_CEILING
P=Path(__file__).resolve().parent;DATA=P/'data'
def ts(s):return int(datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
def quote(q,field,side='yes'):
 try:b,a=float(q['b'+field]),float(q['a'+field])
 except (KeyError,TypeError,ValueError):return None
 if not 0<=b<=a<=1:return None
 return (b,a) if side=='yes' else (1-a,1-b)
def capprice(m,ask,side):
 cap=min(Decimal(str(ask))+Decimal('.01'),Decimal('.939'));yes=cap if side=='yes' else 1-cap
 for r in m['price_ranges']:
  lo,hi,step=map(Decimal,(r['start'],r['end'],r['step']))
  if lo<=yes<=hi:
   p=lo+((yes-lo)/step).to_integral_value(rounding=ROUND_FLOOR if side=='yes' else ROUND_CEILING)*step
   if lo<=p<=hi:return float(p if side=='yes' else 1-p)
 raise ValueError('missing valid tick')
def stats(rows):
 eq=peak=dd=0
 for r in rows:eq+=r['stressed_net'];peak=max(peak,eq);dd=max(dd,peak-eq)
 return {'trades':len(rows),'wins':sum(r['win'] for r in rows),'losses':sum(not r['win'] for r in rows),'net_after_fees':round(sum(r['net'] for r in rows),4),'net_fees_plus_1c_per_contract':round(sum(r['stressed_net'] for r in rows),4),'max_stressed_drawdown':round(dd,4)}
output={}
for a in ['GOLD','WTI','SILVER']:
 ms=[json.loads(l) for l in (DATA/f'markets_{a}.jsonl').read_text().splitlines()]
 qs={r['ticker']:{c['t']:c for c in r['candles']} for r in map(json.loads,(DATA/f'candles_{a}.jsonl').read_text().splitlines())}
 series=json.loads((DATA/f'series_{a}.json').read_text());assert series['fee_type']=='quadratic' and float(series['fee_multiplier'])==1
 counts=collections.Counter();trades=[]
 for m in sorted(ms,key=lambda m:m['close_time']):
  close=ts(m['close_time']);candles=qs[m['ticker']]
  for rem in range(10,2,-1):
   T=close-rem*60;q=quote(candles.get(T,{}),'c');counts['decisions']+=1
   if q is None:counts['missing_quote']+=1;continue
   side='yes' if sum(q)>1 else 'no';bid,ask=quote(candles[T],'c',side)
   if not .85-1e-8<=ask<.93-1e-8 or ask-bid>.02+1e-8:counts['price_spread_reject']+=1;continue
   counts['price_spread_pass']+=1
   nxt=quote(candles.get(T+60,{}),'o',side)
   if nxt is None:counts['missing_next_quote']+=1;continue
   entry=nxt[1];cap=capprice(m,ask,side)
   if entry<.85-1e-8 or entry>cap+1e-8:counts['outside_execution_cap']+=1;continue
   fee=math.ceil((.07*2*entry*(1-entry)-1e-12)*10000)/10000
   win=m['result']==side;net=2*(int(win)-entry)-fee
   trades.append(dict(ticker=m['ticker'],decision_ts=T,close_ts=close,side=side,entry=entry,count=2,win=win,fee=fee,net=net,stressed_net=net-.02));break
 output[a]={'markets':len(ms),'markets_with_candles':sum(bool(v) for v in qs.values()),'median_volume':statistics.median(float(m.get('volume_fp') or 0) for m in ms),'filters_until_first_entry':dict(counts),'all':stats(trades),'first_week':stats([r for r in trades if r['close_ts']<ts('2026-09-18T00:00:00Z')]),'second_week':stats([r for r in trades if r['close_ts']>=ts('2026-09-18T00:00:00Z')])}
 (P/f'baseline_trades_{a}.json').write_text(json.dumps(trades,indent=2))
(P/'baseline_results.json').write_text(json.dumps(output,indent=2));print(json.dumps(output,indent=2))
