"""Build causal post-entry minute paths from saved inputs, without live imports."""
from pathlib import Path
import gzip,json,pickle,math,statistics,hashlib,datetime as dt
from collections import Counter
from functools import lru_cache
P=Path(__file__).resolve().parent;ROOT=P.parent;OLD=ROOT/'kalshi_loss_review_2026_10_14'
CRYPTO=('BTC','ETH','SOL','XRP')
def load(p):return json.loads(p.read_text())
def ts(s):
 s=s.replace('Z','+00:00')
 return (dt.datetime.strptime(s,'%Y-%m-%dT%H:%M:%S.%f%z') if '.' in s else dt.datetime.fromisoformat(s)).timestamp()
prices={a:{} for a in CRYPTO+('NATGAS','WTI','EURUSD','USDCAD')};revisions=Counter()
for a in CRYPTO:
 for raw in map(json.loads,(OLD/'recent_data'/f'spot_{a}.jsonl').read_text().splitlines()):
  for row in raw['rows']:prices[a][int(row[0])]=float(row[4])
def absorb(d):
 a=d['asset']
 if a not in prices:return
 for row in d['timeseries']:
  if row['t']%60000!=59000:continue
  t=row['t']//60000*60;v=float(row['v'])
  if t in prices[a] and prices[a][t]!=v:revisions[a]+=1
  prices[a][t]=v
for f in sorted((OLD/'noncrypto_data/charts').glob('*.gz')):
 absorb(json.load(gzip.open(f))['response']['live_data']['details'])
for f in sorted((P/'fresh').glob('*_chart.json')):absorb(load(f)['response']['live_data']['details'])
markets={m['ticker']:m for n in ('recent_features.pickle','noncrypto_features.pickle') for m in pickle.loads((OLD/n).read_bytes())}
for f in sorted((P/'fresh').glob('*_market.json')):
 raw=load(f)['response']['market'];t=raw['ticker'];a=t.split('-')[0][2:-3]
 candles=load(P/'fresh'/(t+'_candles.json'))['response']['candlesticks']
 cc={c['end_period_ts']:dict(t=c['end_period_ts'],**{x+y:c[f]['%s_dollars'%k] for x,f in [('b','yes_bid'),('a','yes_ask')] for y,k in [('o','open'),('c','close')]}) for c in candles}
 markets[t]=dict(ticker=t,asset=a,start=ts(raw['open_time']),close=ts(raw['close_time']),settled=ts(raw['settlement_ts']),result=1 if raw['result']=='yes' else -1,ranges=raw['price_ranges'],candles=cc,multiplier=1,features=[])

def required(asset,lane,rem,current=False):
 base=3 if lane=='premium_z3' else 2
 if asset in ('EURUSD','USDCAD'):return max(.5,(2.75 if lane=='premium_z3' else 1.75)+.5*math.log2(rem/5))
 if asset=='WTI':return 2.5 if lane=='premium_z3' else 1.5
 if current and asset=='BTC':return 3
 return base
@lru_cache(None)
def values_at(asset,t):
 try:return tuple(prices[asset][k] for k in range(t-3660,t,60))
 except KeyError:return None
@lru_cache(None)
def source(asset,t):
 v=values_at(asset,t)
 if not v:return None
 sig=statistics.pstdev(math.log(y/x) for x,y in zip(v,v[1:]))
 return (v[-1],sig) if sig>0 else None
def bid(c,side,suffix):
 if not c:return None
 try:b,a=float(c['b'+suffix]),float(c['a'+suffix])
 except (KeyError,ValueError,TypeError):return None
 if not 0<=b<=a<=1:return None
 return b if side==1 else 1-a
def decorate(r):
 m=markets[r['ticker']];r['ranges']=m['ranges'];r['multiplier']=m['multiplier'];paths=[]
 for t in range((int(r['entered'])//60+1)*60,int(r['close']),60):
  src=source(r['asset'],t);rem=(r['close']-t)/60
  d=dict(t=t,rem=rem,z=None,z_frozen_sigma=None,spot=None,sigma=None,
      bid_close=bid(m['candles'].get(t),r['side'],'c'),
      bid_next_open=bid(m['candles'].get(t+60),r['side'],'o'),
      bid_delayed_open=bid(m['candles'].get(t+120),r['side'],'o'))
  spot_only=prices[r['asset']].get(t-60)
  if spot_only is not None:
   d['z_frozen_sigma']=r['side']*(spot_only/r['reference']-1)/(r['entry_sigma']*math.sqrt(rem))
  if src:
   spot,sig=src;move=r['side']*(spot/r['reference']-1)
   d.update(spot=spot,sigma=sig,z=move/(sig*math.sqrt(rem)),z_frozen_sigma=move/(r['entry_sigma']*math.sqrt(rem)))
  paths.append(d)
 r['path']=paths
 return r

actual=[];audit=[]
for o in load(P/'actual_orders.json'):
 s=o['signal'];m=markets[o['ticker']];t=s['decision_ts'];vals=values_at(o['asset'],t)
 hashmatch=None
 if vals and s.get('underlying_evidence',{}).get('values_sha256'):
  hashmatch=hashlib.sha256(json.dumps(list(vals),separators=(',',':')).encode()).hexdigest()==s['underlying_evidence']['values_sha256']
 src=source(o['asset'],t)
 audit.append(dict(ticker=o['ticker'],input_hash_match=hashmatch,entry_spot_matches=bool(src and abs(src[0]-s['spot'])<1e-10),entry_sigma_delta=src[1]-s['sigma'] if src else None))
 r=dict(ticker=o['ticker'],asset=o['asset'],lane=o['lane'],t=t,
   entered=s.get('execution_ack_ts',o['created_ts']),entry_time_source='ack' if 'execution_ack_ts' in s else 'durable_intent',
   close=o['close_ts'],settled=m['settled'],side=1 if o['side']=='yes' else -1,
   quantity=float(o['filled']),cost=float(o['cost']),entry_fee=float(o['fees']),entry_friction=0,
   hold_net=float(o['pnl']),won=int(o['side']==o['result']),entry_z=s['z'],entry_sigma=s['sigma'],
   entry_gate=s.get('required_z',required(o['asset'],o['lane'],s['remaining_minutes'])),reference=s['strike_proxy'])
 assert abs(r['quantity']*r['won']-r['cost']-r['entry_fee']-r['hold_net'])<1e-7
 actual.append(decorate(r))

replay=[]
for x in json.load(gzip.open(OLD/'all_recent_ledgers.json.gz'))['fees1c']['btc_z300']:
 m=markets[x['ticker']];v=values_at(x['asset'],x['t']);src=source(x['asset'],x['t'])
 assert src and v
 r=dict(ticker=x['ticker'],asset=x['asset'],lane=x['lane'],t=x['t'],entered=x['entered'],entry_time_source='modeled_plus3s',
   close=x['close'],settled=x['settled'],side=x['side'],quantity=x['quantity'],cost=x['entry']*x['quantity'],
   entry_fee=x['fee'],entry_friction=x['quantity']*.01,hold_net=x['net'],won=x['won'],entry_z=x['z'],
   entry_sigma=src[1],entry_gate=required(x['asset'],x['lane'],x['rem'],True),reference=prices[x['asset']][int(m['start'])-60])
 replay.append(decorate(r))

cohorts=dict(actual=[r for r in actual if r['asset']!='USDCAD'],paused_cad=[r for r in actual if r['asset']=='USDCAD'],current_replay=replay)
with gzip.open(P/'paths.json.gz','wt') as f:json.dump(cohorts,f)
coverage={k:dict(trades=len(v),wins=sum(r['won'] for r in v),hold_net=sum(r['hold_net'] for r in v),minutes=sum(len(r['path']) for r in v),missing_source=sum(p['z'] is None for r in v for p in r['path']),missing_bid=sum(p['bid_close'] is None or p['bid_next_open'] is None for r in v for p in r['path']),one_minute_entries=sum(r['close']-r['t']==60 for r in v),quantities=dict(Counter(r['quantity'] for r in v))) for k,v in cohorts.items()}
(P/'coverage.json').write_text(json.dumps(dict(cohorts=coverage,entry_input_audit=audit,chart_revisions=dict(revisions)),indent=2)+'\n')
print(json.dumps(coverage,indent=2))
