"""Offline full-input replay for predeclared new-market candidates; no orders."""
import sys,json,gzip,math,pickle,statistics
from pathlib import Path
from collections import Counter,defaultdict
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'extension'))
import replay as r
from pull import ts
OLD_END=ts('2026-10-08T00:00:00Z');END=ts('2026-10-09T15:00:00Z')
SCENARIOS=(('plus1c','opening',.01),('plus2c','opening',.02),('cap_plus1c','cap_cost',.01),('delay60_plus1c','delay60',.01))
def save(n,o):(P/n).write_text(json.dumps(o,indent=2,allow_nan=False)+'\n')
def rule(profile):
 def q(f,lane,use_z=True):
  lane_rule=r.LANES[lane]
  low=1 if profile=='eurusd_transfer' else 3
  threshold=max(.5,(1.75 if lane==r.Z2 else 2.75)+.5*math.log2(f['rem']/5)) if profile=='eurusd_transfer' else lane_rule.z
  return low<=f['rem']<=10 and float(lane_rule.floor)-1e-8<=f['ask']<float(lane_rule.ceiling)-1e-8 and f['ask']-f['bid']<=.02000001 and (not use_z or f['z']>=threshold)
 return q
def study(markets,assets,kind,start):
 r.ASSET_ORDER=tuple(dict.fromkeys(r.ASSET_ORDER+tuple(assets)))
 output={};ledgers={}
 for asset in assets:
  ms=[m for m in markets if m['asset']==asset]
  profiles=('standard','eurusd_transfer') if kind=='noncrypto' and asset in ('USDJPY','GBPUSD','AUDUSD','USDCAD') else ('standard',)
  for profile in profiles:
   r.qualifies=rule(profile);one={}
   for label,mode,stress in SCENARIOS:
    candidates,counts=r.opportunities(ms,mode)
    if kind=='indices':
     # One market per index expiry. Keep strike only for audit; use event as ownership key.
     candidates.sort(key=lambda x:(x['entered'],0 if x['lane']==r.Z2 else 1,x['ask'],-x['z'],x['ticker']))
     candidates=[dict(x,market_ticker=x['ticker'],ticker=x['event'] if 'event' in x else next(m['event'] for m in ms if m['ticker']==x['ticker'])) for x in candidates]
    periods={}
    for period,lo,hi in [('full',start,END),('old',start,OLD_END),('new_oct8_9',OLD_END,END),('early',start,ts('2026-10-03T00:00:00Z')),('middle',ts('2026-10-03T00:00:00Z'),OLD_END)]:
     selected=[x for x in candidates if lo<=x['close']<hi]
     accepted,blocked=r.portfolio(selected,stress)
     m=r.metrics(accepted);m['blocked']=blocked;m['lanes']={l:r.metrics([x for x in accepted if x['lane']==l]) for l in (r.Z2,r.Z3)}
     periods[period]=m;ledgers[f'{asset}/{profile}/{label}/{period}']=accepted
     for x in accepted:
      assert x['entered']<x['close'] and x['entry']<=x['cap']+1e-8
      assert abs(x['net']-(5*(x['won']-x['entry'])-x['fee']-5*stress))<1e-8
     assert len({x['ticker'] for x in accepted})==len(accepted)
    one[label]=dict(periods=periods,counts=counts)
   output[asset+'/'+profile]=one
   print(kind,asset,profile,{x:{p:(y['trades'],y['wins'],y['losses'],y['net']) for p,y in z['periods'].items() if p in ('full','new_oct8_9')} for x,z in one.items()},flush=True)
 save(kind+'_results.json',output)
 with gzip.open(P/(kind+'_ledgers.json.gz'),'wt') as f:json.dump(ledgers,f)
 return output
def crypto_data():
 assets=('DOGE','HYPE','NEAR','ZEC','BNB');D=P/'crypto/data';markets=[];coverage={}
 for a in assets:
  counts=Counter();prices={int(x[0]):float(x[4]) for b in map(json.loads,(D/('spot_'+a+'.jsonl')).read_text().splitlines()) for x in b['rows']}
  quotes={x['ticker']:{int(c['t']):c for c in x['candles']} for x in map(json.loads,(D/('candles_'+a+'.jsonl')).read_text().splitlines())}
  meta=json.loads((D/('series_'+a+'.json')).read_text())['response']['series'];assert meta['fee_type']=='quadratic'
  start=ts('2026-10-01T00:00:00Z')
  for raw in json.loads((D/('markets_'+a+'.json')).read_text()):
   op,cl=ts(raw['open_time']),ts(raw['close_time'])
   if not start<=cl<END:continue
   assert cl-op==900 and raw['result'] in ('yes','no') and float(raw['notional_value_dollars'])==1
   m=dict(ticker=raw['ticker'],asset=a,start=op,close=cl,settled=ts(raw['settlement_ts']),result=1 if raw['result']=='yes' else -1,ranges=raw['price_ranges'],candles=quotes.get(raw['ticker'],{}),multiplier=meta['fee_multiplier'],features=[],quote_features=[])
   counts['markets']+=1
   for rem in range(10,2,-1):
    T=cl-rem*60;q=r.quote(m['candles'].get(T))
    if q is None:counts['missing_quote']+=1;continue
    side=1 if sum(q)>1 else -1;bid,ask=q if side==1 else (1-q[1],1-q[0]);f=dict(t=T,rem=rem,side=side,bid=bid,ask=ask);m['quote_features'].append(dict(f))
    extra=r.feature(prices,op,T,rem,side)
    if extra is None:
     counts['incomplete_or_zero_volatility']+=1
     if any(rule('standard')(dict(f,z=10),l) for l in (r.Z2,r.Z3)):counts['price_qualified_spot_gap']+=1
     continue
    f.update(extra);m['features'].append(f);counts['feature_minutes']+=1
   markets.append(m)
  counts['missing_spot_minutes']=sum(t not in prices for t in range(start,END,60));counts['expected_spot_minutes']=(END-start)//60;coverage[a]=dict(counts)
 save('crypto_coverage.json',coverage);return markets,assets
def index_data():
 assets=('INX','NDQ','DJI');series_names=('KXINXU','KXNASDAQ100U','KXDJI');D=P/'indices/data';markets=[];coverage={};integrity=[]
 catalog={s['ticker']:s for s in json.loads((P/'series.json').read_text())['series']}
 for a,series in zip(assets,series_names):
  dest=D/series;counts=Counter();charts={};quotes={}
  for file in dest.glob('*_chart.json.gz'):
   with gzip.open(file,'rt') as f:raw=json.load(f)
   det=raw['response']['live_data']['details'];assert det['asset']==a
   points={int(x['t'])//1000:float(x['v']) for x in det['timeseries'] if int(x['t'])%60000==0 and float(x['v'])>0}
   charts[det['event_ticker']]=points
  for file in dest.glob('*_quotes.json'):
   for raw in json.loads(file.read_text()):
    candles={}
    for c in raw['candlesticks']:
     x=dict(t=c['end_period_ts'],v=float(c.get('volume_fp') or 0))
     for field,prefix in [('yes_bid','b'),('yes_ask','a')]:
      for phase,suffix in [('open','o'),('close','c')]:x[prefix+suffix]=c.get(field,{}).get(phase+'_dollars')
     candles[x['t']]=x
    quotes[raw['market_ticker']]=candles
  for raw in json.loads((dest/'markets.json').read_text()):
   if raw['ticker'] not in quotes:continue
   op,cl=ts(raw['open_time']),ts(raw['close_time']);bars=charts.get(raw['event_ticker'],{})
   strike=float(raw['floor_strike']);assert strike>0 and raw['result'] in ('yes','no')
   m=dict(ticker=raw['ticker'],event=raw['event_ticker'],asset=a,start=op,close=cl,settled=ts(raw['settlement_ts']),result=1 if raw['result']=='yes' else -1,ranges=raw['price_ranges'],candles=quotes[raw['ticker']],multiplier=catalog[series]['fee_multiplier'],features=[],quote_features=[])
   if cl in bars and raw.get('expiration_value'):
    final=float(raw['expiration_value']);integrity.append(dict(ticker=m['ticker'],event=m['event'],chart=bars[cl],expiration=final,difference=abs(bars[cl]-final)))
   counts['markets_with_quotes']+=1
   for rem in range(10,2,-1):
    T=cl-rem*60;q=r.quote(m['candles'].get(T))
    if q is None:counts['missing_quote']+=1;continue
    if sum(c['v'] for t,c in m['candles'].items() if t<=T)<5:counts['insufficient_prior_volume']+=1;continue
    side=1 if sum(q)>1 else -1;bid,ask=q if side==1 else (1-q[1],1-q[0]);f=dict(t=T,rem=rem,side=side,bid=bid,ask=ask)
    keys=list(range(T-3660,T,60))
    if not all(t in bars for t in keys):counts['missing_consecutive_minutes']+=1;continue
    vals=[bars[t] for t in keys];sigma=statistics.pstdev(math.log(y/x) for x,y in zip(vals,vals[1:]))
    if sigma<=0:counts['zero_sigma']+=1;continue
    f.update(z=side*(vals[-1]/strike-1)/(sigma*math.sqrt(rem)),sigma=sigma,ref=strike,spot=vals[-1]);m['features'].append(f);counts['feature_minutes']+=1
   markets.append(m)
  coverage[a]=dict(counts)
 save('index_coverage.json',coverage);save('index_integrity.json',dict(checks=len(integrity),mismatches=[x for x in integrity if x['difference']>.011],unique_events=len({x['event'] for x in integrity})))
 return markets,assets
if __name__=='__main__':
 kind=sys.argv[1]
 if kind=='noncrypto':
  markets,_=r.load_data();study(markets,r.ASSETS,kind,ts('2026-09-11T00:00:00Z'))
 elif kind=='crypto':
  markets,assets=crypto_data();study(markets,assets,kind,ts('2026-10-01T00:00:00Z'))
 elif kind=='indices':
  markets,assets=index_data();study(markets,assets,kind,ts('2026-09-28T00:00:00Z'))
