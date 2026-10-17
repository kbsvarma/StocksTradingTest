import json,math,statistics,datetime,re,collections
from pathlib import Path
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;D=P/'fresh_data';ET=ZoneInfo('America/New_York')
def ts(s):return int(datetime.datetime.fromisoformat(re.sub(r'\.(\d+)',lambda m:'.'+m.group(1)[:6].ljust(6,'0'),s).replace('Z','+00:00')).timestamp())
def day(t):return datetime.datetime.fromtimestamp(t,ET).date().isoformat()
raw=json.loads((P/'records.json').read_text());logged={(e['asset'],e['decision_ts']):e for e in raw['events'] if isinstance(e.get('z'),(int,float))}
daily=collections.defaultdict(collections.Counter);qualified=collections.defaultdict(set);checks=[];rows=[];bycohort=collections.defaultdict(collections.Counter)
for a in ['BTC','ETH','SOL','XRP']:
 bars={}
 for line in (D/f'spot_{a}.jsonl').read_text().splitlines():
  for r in json.loads(line)['rows']:bars[int(r[0])]=float(r[4])
 stats={}
 for T in range(min(bars)+3660,max(bars)+60,60):
  keys=range(T-3660,T,60)
  if not all(k in bars for k in keys):continue
  vals=[bars[k] for k in keys];sigma=statistics.pstdev(math.log(y/x) for x,y in zip(vals,vals[1:]))
  if sigma>0:stats[T]=(vals[-1],sigma)
 markets={m['ticker']:m for m in map(json.loads,(D/f'markets_{a}.jsonl').read_text().splitlines())}
 for line in (D/f'candles_{a}.jsonl').read_text().splitlines():
  r=json.loads(line);m=markets[r['ticker']];start=ts(m['open_time']);close=ts(m['close_time'])
  for c in r['candles']:
   T=c['t'];rem=(close-T)/60
   if not 3<=rem<=10 or day(T)<'2026-09-23' or day(T)>'2026-10-01':continue
   d=day(T);co='earlier' if d<='2026-09-29' else 'recent';st=daily[d];bc=bycohort[co];st['minutes']+=1;bc['minutes']+=1
   try:b,ask=float(c['bc']),float(c['ac'])
   except (TypeError,ValueError):st['missing_quote']+=1;continue
   if T not in stats or start-60 not in bars:st['missing_spot']+=1;continue
   side=1 if b+ask>1 else -1;bid,ask=(b,ask) if side==1 else (1-ask,1-b)
   spot,sigma=stats[T];z=side*(spot/bars[start-60]-1)/(sigma*math.sqrt(rem))
   if (a,T) in logged:
    old=logged[(a,T)];checks.append(dict(asset=a,T=T,z_delta=abs(z-old['z']),spot_delta=abs(spot-old['spot']),strike_delta=abs(bars[start-60]-old['strike_proxy']),ask_delta=abs(ask-old['signal_ask'])))
   inband=.85-1e-8<=ask<.93-1e-8 and ask-bid<=.02000001
   if inband:st['price_pass']+=1;bc['price_pass']+=1
   if z>=2:
    st['z2_any_price']+=1;bc['z2_any_price']+=1
    if ask>=.93-1e-8:st['z2_price_too_high']+=1;bc['z2_price_too_high']+=1
    elif ask<.85-1e-8:st['z2_price_too_low']+=1;bc['z2_price_too_low']+=1
    elif ask-bid>.02000001:st['z2_spread']+=1;bc['z2_spread']+=1
    else:st['all_pass']+=1;bc['all_pass']+=1;qualified[d].add(r['ticker'])
   rows.append(dict(asset=a,T=T,ticker=r['ticker'],side=side,z=z,ask=ask,bid=bid,sigma=sigma,spot=spot,strike_proxy=bars[start-60],inband=inband))
out=dict(daily={d:dict(v,qualifying_markets=len(qualified[d])) for d,v in sorted(daily.items())},cohorts={k:dict(v) for k,v in bycohort.items()},matched_log_observations=len(checks),mismatch_counts={f:sum(c[f]>1e-7 for c in checks) for f in ['z_delta','spot_delta','strike_delta','ask_delta']},max_z_difference=max(c['z_delta'] for c in checks),mismatches=[c for c in checks if c['z_delta']>1e-7 or c['ask_delta']>1e-7])
(P/'independent_results.json').write_text(json.dumps(out,indent=2));(P/'independent_rows.json').write_text(json.dumps(rows))
print(json.dumps(out,indent=2))
