"""Union archived research inputs; all outputs stay in this experiment."""
from pathlib import Path
import json,gzip,pickle,math,hashlib,collections,datetime,time,requests,sys
import numpy as np
P=Path(__file__).resolve().parent;R=P.parent
ASSETS=('BTC','ETH','SOL','XRP','COPPER','NATGAS','EURUSD','WTI','USDCAD');CRYPTO=ASSETS[:4]
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def ts(s):return datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def load():
 sources=[('kalshi_btc_eth_quant_2026_10_02/features.pickle',False),('kalshi_metals_research_2026_10_11/features.pickle',True),('kalshi_frequency_expansion_2026_10_12/financial_inputs/features.pickle',True),('kalshi_finance_expansion_2026_10_14/fx/features.pickle',True),('kalshi_loss_review_2026_10_14/recent_features.pickle',False),('kalshi_loss_review_2026_10_14/noncrypto_features.pickle',False)]
 markets={};prices={a:{} for a in ASSETS};manifest=[]
 for name,zero in sources:
  path=R/name;rows=pickle.loads(path.read_bytes());manifest.append(dict(path=name,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
  for m in rows:
   a=m['asset']
   if a not in ASSETS:continue
   mm={k:m[k] for k in ('ticker','asset','start','close','settled','result','ranges','candles','features')};mm['multiplier']=m.get('multiplier',1)
   if zero:mm['result']=1 if m['result'] else -1
   markets[m['ticker']]=mm
   for f in m['features']:
    prices[a][int(f['t'])-60]=f['spot'];prices[a][int(m['start'])-60]=f['ref']
 for root in ['kalshi_restart_audit_2026_09_12/fresh_data','kalshi_parallel_design_2026_10_02/fresh_data','kalshi_execution_audit_2026_10_02/fresh_data','kalshi_btc_eth_quant_2026_10_02/fresh_data','kalshi_loss_review_2026_10_14/recent_data']:
  for a in CRYPTO:
   f=R/root/f'spot_{a}.jsonl'
   if not f.exists():continue
   for x in map(json.loads,f.read_text().splitlines()):
    for row in x['rows']:prices[a][int(row[0])]=float(row[4])
 # Reuse exact minute bars already extracted from saved one-second charts.
 for root in ['kalshi_metals_research_2026_10_11','kalshi_frequency_expansion_2026_10_12/financial_inputs','kalshi_finance_expansion_2026_10_14/fx']:
  f=R/root/'bars.pickle'
  if f.exists():
   for a,bb in pickle.loads(f.read_bytes()).items():
    if a in prices:prices[a].update(bb)
 freshroots=[R/'kalshi_z_exit_research_2026_10_14/fresh',P/'fresh']
 def absorb_market(p):
  raw=json.loads(p.read_text())['response']['market'];ticker=raw['ticker'];a=ticker.split('-')[0][2:-3]
  f=p.parent/(ticker+'_candles.json');cc={}
  if f.exists():
   for c in json.loads(f.read_text())['response']['candlesticks']:
    cc[c['end_period_ts']]={**{'t':c['end_period_ts']},**{x+y:c.get(n,{}).get(k+'_dollars') for x,n in [('b','yes_bid'),('a','yes_ask')] for y,k in [('o','open'),('c','close'),('h','high'),('l','low')]}}
  markets[ticker]=dict(ticker=ticker,asset=a,start=ts(raw['open_time']),close=ts(raw['close_time']),settled=ts(raw['settlement_ts']),result=1 if raw['result']=='yes' else -1,ranges=raw['price_ranges'],candles=cc,multiplier=1,features=[])
 for root in freshroots:
  for p in root.glob('*_market.json'):absorb_market(p)
 orders=json.loads((P/'all_orders.json').read_text());actual=[o for o in orders if o.get('status')=='settled' and float(o.get('filled',0))>0]
 missing=[o for o in actual if o['ticker'] not in markets]
 session=requests.Session();(P/'fresh').mkdir(exist_ok=True)
 def get(name,path,params=None):
  p=P/'fresh'/name
  if p.exists():return json.loads(p.read_text())['response']
  time.sleep(1.5);started=time.time();res=session.get('https://external-api.kalshi.com/trade-api/v2'+path,params=params,timeout=15);res.raise_for_status();d=res.json();p.write_text(json.dumps(dict(started=started,received=time.time(),url=res.url,response=d))+'\n');return d
 for o in missing:
  t=o['ticker'];m=get(t+'_market.json','/markets/'+t)['market'];get(t+'_candles.json','/series/KX'+o['asset']+'15M/markets/'+t+'/candlesticks',dict(start_ts=o['close_ts']-900,end_ts=o['close_ts'],period_interval=1));get(m['event_ticker']+'_chart.json','/live_data/events/'+m['event_ticker'],dict(range='1h'));absorb_market(P/'fresh'/(t+'_market.json'));print('fresh',t,flush=True)
 # Latest noncrypto charts fill endpoints omitted by price-feature gates.
 for root in [R/'kalshi_loss_review_2026_10_14/noncrypto_data/charts']+freshroots:
  for f in sorted(root.glob('*chart.json'))+sorted(root.glob('*.json.gz')):
   x=json.load(gzip.open(f)) if f.suffix=='.gz' else json.loads(f.read_text());d=x.get('response',{}).get('live_data',{}).get('details',{});a=d.get('asset')
   if a not in prices:continue
   for q in d.get('timeseries',[]):
    if q['t']%60000==59000:prices[a][q['t']//60000*60]=float(q['v'])
 # Refresh every feature from complete61-point source; no forward filled inputs.
 stats={};stats_missing=collections.Counter()
 for a,bb in prices.items():
  keys=np.arange(min(bb),max(bb)+60,60,dtype=np.int64);vv=np.array([bb.get(int(t),np.nan) for t in keys]);w=np.lib.stride_tricks.sliding_window_view(np.diff(np.log(vv)),60);sig=np.std(w,axis=1);ok=np.isfinite(w).all(axis=1)&(sig>0)
  stats[a]={int(keys[i+60]+60):(float(vv[i+60]),float(sig[i])) for i in np.flatnonzero(ok)}
 # Archived features were themselves constructed from complete61-point windows.
 # Retain those independently archived values where the union of raw files has a
 # gap. This is explicit archived evidence, never interpolation/forward filling.
 fallback=collections.Counter()
 for m in markets.values():
  for f in m['features']:
   if f['t'] not in stats[m['asset']]:stats[m['asset']][f['t']]=(f['spot'],f['sigma']);fallback[m['asset']]+=1
 save('feature_fallback.json',dict(fallback))
 for m in markets.values():
  old=m['features'];ff=[];a=m['asset'];ref=prices[a].get(int(m['start'])-60)
  for t in range(int(m['start'])+120,int(m['close']),60):
   c=m['candles'].get(t);s=stats[a].get(t)
   if c is None or s is None or ref is None:stats_missing[a]+=1;continue
   try:b,ask=float(c['bc']),float(c['ac'])
   except (TypeError,KeyError,ValueError):continue
   if not 0<=b<=ask<=1:continue
   side=1 if b+ask>1 else -1;rem=(m['close']-t)/60
   ff.append(dict(t=t,rem=rem,side=side,bid=b if side==1 else 1-ask,ask=ask if side==1 else 1-b,z=side*(s[0]/ref-1)/(s[1]*math.sqrt(rem)),spot=s[0],ref=ref,sigma=s[1]))
  m['features']=ff
 cohorts=[];audit=[]
 for o in actual:
  s=o['signal'];m=markets[o['ticker']];r=dict(ticker=o['ticker'],asset=o['asset'],lane=o.get('lane',s.get('lane','cushion_z2')),t=s['decision_ts'],entered=s.get('execution_ack_ts',o['created_ts']),close=o['close_ts'],settled=m['settled'],side=1 if o['side']=='yes' else -1,quantity=float(o['filled']),cost=float(o['cost']),entry_fee=float(o['fees']),entry_friction=0,hold_net=float(o['pnl']),won=int(o['side']==o['result']),entry_z=s['z'],entry_sigma=s['sigma'],reference=s['strike_proxy'],entry_spot=s['spot'],entry_gate=s.get('required_z'),ranges=m['ranges'])
  if r['entry_gate'] is None:
   premium=r['lane']=='premium_z3';a=r['asset'];base=(2.5 if premium else 1.5) if a in ('WTI','COPPER') else (2.75 if premium else 1.75) if a in ('EURUSD','USDCAD') else (3 if premium else 2)
   r['entry_gate']=max(.5,base+.5*math.log2(s['remaining_minutes']/5)) if a in ('EURUSD','USDCAD') else base
  assert abs(r['quantity']*r['won']-r['cost']-r['entry_fee']-r['hold_net'])<1e-7
  sample=[stats[r['asset']].get(t) for t in range(int(r['entered'])//60*60+60,int(r['close']),60)];cohorts.append(r);audit.append(dict(ticker=r['ticker'],asset=r['asset'],won=r['won'],hold_net=r['hold_net'],minutes=len(sample),missing=sum(x is None for x in sample),entry_source_delta=stats[r['asset']].get(r['t'],[None])[0]-r['entry_spot'] if stats[r['asset']].get(r['t']) else None))
 with gzip.open(P/'inputs.pkl.gz','wb',compresslevel=1) as f:pickle.dump(dict(markets=markets,prices=prices,stats=stats,actual=cohorts),f)
 save('actual_entries.json',cohorts);save('actual_coverage.json',audit);save('input_manifest.json',manifest)
 save('market_coverage.json',{a:dict(markets=len([m for m in markets.values() if m['asset']==a]),first=min(m['close'] for m in markets.values() if m['asset']==a),last=max(m['close'] for m in markets.values() if m['asset']==a),features=sum(len(m['features']) for m in markets.values() if m['asset']==a),missing_minutes=stats_missing[a]) for a in ASSETS})
 print('actual',len(cohorts),'missing post-entry source',sum(x['missing'] for x in audit),'missing loss source',[x for x in audit if not x['won'] and x['missing']],flush=True)
if __name__=='__main__':load()
