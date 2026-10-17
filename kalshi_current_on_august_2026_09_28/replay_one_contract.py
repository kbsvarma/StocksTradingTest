"""Read-only current-design historical replay; no credentials or order calls."""
import re
import json, math, statistics, bisect, sys, datetime, collections
from pathlib import Path
from zoneinfo import ZoneInfo
from decimal import Decimal
from unittest.mock import patch
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'reference'))
import market_data,engine
D=P/'fresh_data'; ET=ZoneInfo('America/New_York')
START=1786173060; END=1786514400 # first old entry minute through last old market close
N=1
market_data.CONTRACTS=1

def ts(s):return datetime.datetime.fromisoformat(re.sub(r'\.(\d+)',lambda m:'.'+m.group(1)[:6].ljust(6,'0'),s).replace('Z','+00:00')).timestamp()
def day(t):return datetime.datetime.fromtimestamp(t,ET).date().isoformat()
def fee(p):return math.ceil((.07*N*p*(1-p)-1e-12)*10000)/10000
markets={}; candles={}; spots={};books=collections.defaultdict(list)
for a in ('BTC','ETH','SOL','XRP'):
 for s in (D/f'markets_{a}.jsonl').read_text().splitlines():
  m=json.loads(s); m['asset']=a; markets[m['ticker']]=m
 for s in (D/f'candles_{a}.jsonl').read_text().splitlines():
  r=json.loads(s);candles[r['ticker']]={c['t']:c for c in r['candles']}
 bars={}
 for s in (D/f'spot_{a}.jsonl').read_text().splitlines():
  for r in json.loads(s)['rows']:bars[int(r[0])]=float(r[4])
 spots[a]=bars
for s in (P/'books.jsonl').open():
 r=json.loads(s);books[r['ticker']].append(r)
for rr in books.values():rr.sort(key=lambda r:r['ts'])
btimes={t:[r['ts'] for r in rr] for t,rr in books.items()}
signals=[];quality=collections.Counter()
for ticker,m in markets.items():
 start=int(ts(m['open_time'])); close=int(ts(m['close_time']));a=m['asset'];bars=spots[a]
 for T in range(start+5*60,close-2*60,60):
  if not START<=T<END:continue
  quality['decision_minutes']+=1
  c=candles[ticker].get(T)
  try:b,ask=float(c['bc']),float(c['ac'])
  except (TypeError,KeyError,ValueError):quality['missing_quote']+=1;continue
  if not 0<=b<=ask<=1:continue
  side='yes' if b+ask>1 else 'no';bid,ask=(b,ask) if side=='yes' else (1-ask,1-b)
  if not .85-1e-8<=ask<.93-1e-8 or ask-bid>.02+1e-8:continue
  quality['price_qualified_minutes']+=1
  keys=list(range(T-61*60,T,60))
  if start-60 not in bars or not all(k in bars for k in keys):quality['missing_spot_history']+=1;continue
  vals=[bars[k] for k in keys];sigma=statistics.pstdev(math.log(y/x) for x,y in zip(vals,vals[1:]))
  if sigma<=0:continue
  rem=(close-T)/60;z=(1 if side=='yes' else -1)*(bars[T-60]/bars[start-60]-1)/(sigma*math.sqrt(rem))
  if z<2:continue
  s=dict(ticker=ticker,asset=a,side=side,z=z,decision_ts=T,remaining_minutes=rem,signal_bid=bid,signal_ask=ask,price_ranges=m['price_ranges'])
  _,cap=engine.limit_price(s);s['cap']=float(cap);signals.append(s)
signals.sort(key=lambda s:(s['decision_ts'],s['asset']))

def depth(book,s):
 qty=0;cost=0
 for price,n in sorted((1-float(p),float(n)) for p,n in book.get('no' if s['side']=='yes' else 'yes',[])):
  if n<=0:continue
  if price<.85-1e-8:return None,'below_floor'
  if price>s['cap']+1e-8:break
  got=min(N-qty,n);qty+=got;cost+=got*price
  if qty>=N:return cost/N,None
 return None,'insufficient_depth_inside_cap'

def run(mode):
 done=set();rows=[];pending=[];realized=collections.defaultdict(float);skips=collections.Counter();validated=0
 for s in signals:
  t=s['ticker'];T=s['decision_ts'];m=markets[t]
  if t in done:continue
  if mode=='book':
   times=btimes.get(t,[]);i=bisect.bisect_left(times,T+.3)
   if i==len(times) or times[i]>T+35:skips['no_snapshot_within_35s']+=1;continue
   book=books[t][i];entry_ts=book['ts'];price,reason=depth(book,s)
   if reason:skips[reason]+=1;continue
   # Verify against unmodified deployed scanner with historical GET fixtures.
   a=s['asset'];c=candles[t][T]
   def get(url):
    if '/markets?' in url:return {'markets':[m]}
    if '/candlesticks?' in url:return {'candlesticks':[{'end_period_ts':T,'yes_bid':{'close_dollars':c['bc']},'yes_ask':{'close_dollars':c['ac']}}]}
    if 'coinbase.com' in url:return [[k,0,0,0,v,0] for k,v in spots[a].items() if T-95*60<=k<=T]
    if '/orderbook?' in url:return {'orderbook_fp':{'yes_dollars':book['yes'],'no_dollars':book['no']}}
    raise ValueError(url)
   with patch.object(market_data,'get',get),patch.object(market_data.time,'time',lambda:entry_ts):
    actual=market_data.scan(a,T,set())
   assert actual['status']=='paper_fill',actual
   assert actual['side']==s['side'] and math.isclose(actual['z'],s['z'],abs_tol=1e-10)
   assert math.isclose(actual['entry'],price,abs_tol=1e-10);validated+=1
  else:
   c=candles[t].get(T+60);field='o' if mode=='next_open' else 'c'
   try:price=float(c['a'+field]) if s['side']=='yes' else 1-float(c['b'+field])
   except (KeyError,ValueError,TypeError):skips['quote_missing']+=1;continue
   if not .85-1e-8<=price<=s['cap']+1e-8:skips['quote_outside_cap']+=1;continue
   entry_ts=T+.3 if mode=='next_open' else T+60
  for r in pending[:]:
   if r['settled_ts']<=entry_ts:realized[day(r['settled_ts'])]+=r['pnl'];pending.remove(r)
  if len(pending)>=4 or any(r['asset']==s['asset'] for r in pending):skips['exposure']+=1;continue
  if realized[day(entry_ts)]-(len(pending)+1)*.95 < -5:skips['daily_budget']+=1;continue
  won=m['result']==s['side'];f=fee(price);pnl=N*(int(won)-price)-f
  settle=ts(m.get('settlement_ts') or m.get('settlement_time') or m['close_time'])
  assert settle>=entry_ts
  r=dict(s,entry_ts=entry_ts,price=price,count=N,fee=f,win=won,pnl=pnl,settled_ts=settle)
  rows.append(r);pending.append(r);done.add(t)
 daily=collections.defaultdict(float)
 for r in rows:daily[day(r['settled_ts'])]+=r['pnl']
 equity=0;peak=0;dd=0
 for r in sorted(rows,key=lambda r:r['settled_ts']):equity+=r['pnl'];peak=max(peak,equity);dd=max(dd,peak-equity)
 assert len(done)==len(rows)
 return dict(mode=mode,trades=len(rows),wins=sum(r['win'] for r in rows),losses=sum(not r['win'] for r in rows),pnl=sum(r['pnl'] for r in rows),fees=sum(r['fee'] for r in rows),drawdown=dd,daily=dict(daily),skips=dict(skips),scanner_validations=validated,rows=rows)
result=dict(start=START,end=END,contracts=N,markets_loaded=len(markets),signals=len(signals),signal_markets=len({s['ticker'] for s in signals}),book_snapshots=sum(map(len,books.values())),quality=dict(quality),scenarios=[run(mode) for mode in ('book','next_open','next_close')])
(P/'results_one_contract.json').write_text(json.dumps(result,indent=2))
print(json.dumps({**result,'scenarios':[{k:v for k,v in r.items() if k!='rows'} for r in result['scenarios']]},indent=2))
