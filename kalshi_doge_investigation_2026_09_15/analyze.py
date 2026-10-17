import json,math,statistics,collections
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal,ROUND_CEILING,ROUND_FLOOR
P=Path(__file__).resolve().parent;data=P/'data'
def ts(s):return int(datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
markets=[json.loads(x) for x in (data/'markets_DOGE.jsonl').read_text().splitlines()]
quotes={r['ticker']:{x['t']:x for x in r['candles']} for r in map(json.loads,(data/'candles_DOGE.jsonl').read_text().splitlines())}
bars={int(x[0]):float(x[4]) for r in map(json.loads,(data/'spot_DOGE.jsonl').read_text().splitlines()) for x in r['rows']}
start=ts('2026-09-01T00:00:00Z');end=ts('2026-09-15T00:00:00Z');split=ts('2026-09-08T00:00:00Z')
def fee(p):return math.ceil((.07*p*(1-p)-1e-12)*10000)/10000
def qside(q,side,field):
 try:b,a=float(q['b'+field]),float(q['a'+field])
 except (KeyError,TypeError,ValueError):return None
 if not 0<=b<=a<=1:return None
 return (b,a) if side=='yes' else (1-a,1-b)
def stats(rows):
 pnls=[x['net'] for x in rows];stress=[x-.01 for x in pnls];equity=peak=dd=0
 for x in stress:equity+=x;peak=max(peak,equity);dd=max(dd,peak-equity)
 return {'trades':len(rows),'wins':sum(x['win'] for x in rows),'net_after_fee':round(sum(pnls),6),'net_after_fee_plus_1c':round(sum(stress),6),'avg_cents_after_fee_plus_1c':round(100*statistics.mean(stress),4) if stress else None,'max_drawdown_plus_1c':round(dd,6)}
output={'window_utc':['2026-09-01','2026-09-15 exclusive'],'markets':len(markets),'expected_15m_markets':1344,'settlement_results':dict(collections.Counter(m['result'] for m in markets)),'markets_per_day':dict(collections.Counter(m['close_time'][:10] for m in markets)),'volume_median_contracts':statistics.median(float(m.get('volume_fp') or 0) for m in markets),'markets_with_volume':sum(float(m.get('volume_fp') or 0)>0 for m in markets),'coinbase_missing_minutes':sum(t not in bars for t in range(start,end,60)),'expected_coinbase_minutes':(end-start)//60}
for mode in ('strict','carry_forward_diagnostic'):
 prices=dict(bars)
 if mode!='strict':
  last=None
  for t in range(start-7200,end,60):
   if t in bars:last=bars[t]
   elif last is not None:prices[t]=last
 counters=collections.Counter();trades=[];eligible_markets=set();complete_market=set()
 for m in sorted(markets,key=lambda m:m['close_time']):
  op,cl=ts(m['open_time']),ts(m['close_time']);cs=quotes.get(m['ticker'],{});taken=False
  for rem in range(10,2,-1):
   T=cl-rem*60;counters['decisions']+=1;q=cs.get(T,{})
   yes=qside(q,'yes','c')
   if yes is None:counters['missing_quote']+=1;continue
   side='yes' if sum(yes)>1 else 'no';bid,ask=qside(q,side,'c')
   if not .85-1e-8<=ask<.93-1e-8 or ask-bid>.02+1e-8:counters['price_spread_reject']+=1;continue
   counters['price_spread_pass']+=1
   keys=list(range(T-3660,T,60))
   if op-60 not in prices or not all(k in prices for k in keys):counters['incomplete_spot']+=1;continue
   counters['complete_spot']+=1;complete_market.add(m['ticker'])
   vals=[prices[k] for k in keys];sigma=statistics.pstdev(math.log(b/a) for a,b in zip(vals,vals[1:]))
   z=(1 if side=='yes' else -1)*(prices[T-60]/prices[op-60]-1)/sigma/math.sqrt(rem) if sigma>0 else 0
   if z<2:counters['z_reject']+=1;continue
   counters['signal_minutes']+=1;eligible_markets.add(m['ticker'])
   if taken:continue
   nxt=qside(cs.get(T+60,{}),side,'o')
   if nxt is None:counters['no_next_quote']+=1;continue
   entry=nxt[1];cap=min(.939,ask+.01)
   # Historical market price ladder, rounded to avoid spending above cap.
   ladder=m.get('price_ranges')
   if not ladder:counters['missing_tick_ladder']+=1;continue
   dc=Decimal(str(cap));yp=dc if side=='yes' else 1-dc;valid=[]
   for rg in ladder:
    lo,hi,step=map(Decimal,(rg['start'],rg['end'],rg['step']))
    if lo<=yp<=hi:
     px=lo+((yp-lo)/step).to_integral_value(rounding=ROUND_FLOOR if side=='yes' else ROUND_CEILING)*step
     if lo<=px<=hi:valid.append(float(px if side=='yes' else 1-px))
   if not valid:counters['tick_reject']+=1;continue
   cap=max(valid)
   if entry<.85-1e-8 or entry>cap+1e-8:counters['next_quote_outside_cap']+=1;continue
   win=m['result']==side;net=int(win)-entry-fee(entry)
   trades.append({'ticker':m['ticker'],'signal_ts':T,'close_ts':cl,'side':side,'z':z,'entry':entry,'win':win,'fee':fee(entry),'net':net});taken=True
 output[mode]={'filters':dict(counters),'markets_with_complete_in_band_data':len(complete_market),'signal_markets':len(eligible_markets),'all':stats(trades),'first_week':stats([x for x in trades if x['close_ts']<split]),'second_week':stats([x for x in trades if x['close_ts']>=split])}
 (P/(mode+'_trades.json')).write_text(json.dumps(trades,indent=2))
(P/'results.json').write_text(json.dumps(output,indent=2));print(json.dumps(output,indent=2))
