"""Matched-hour diagnostic from read-only live receipts. No exchange requests."""
import collections,datetime,hashlib,json,math,sqlite3,statistics,subprocess,sys,time
from pathlib import Path
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;L=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
sys.path.insert(0,str(L))
from lanes import lane_for
ET=ZoneInfo('America/New_York')
ASSETS=('BTC','ETH','SOL','XRP','NATGAS','EURUSD','WTI')
DAYS=[f'2026-10-{x:02d}' for x in range(8,15)]
WEEKDAYS=['2026-10-08','2026-10-09','2026-10-12','2026-10-13']
def inside(ts):
 d=datetime.datetime.fromtimestamp(ts,ET)
 return str(d.date()) in DAYS and 14*60+30<=d.hour*60+d.minute<16*60
def day(ts):return str(datetime.datetime.fromtimestamp(ts,ET).date())
def pct(v,n):return round(100*v/n,2) if n else None
def median(v):return statistics.median(v) if v else None
def preservation():
 names=('kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service','kalshi-weather-early-paper.service')
 return dict(ts=time.time(),hashes={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in L.iterdir() if f.suffix=='.py' or f.name=='dashboard.css'},flags={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in (L/'state').iterdir() if f.is_file() and f.name.startswith(('ARM','HALT'))},services={n:subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True) for n in names})
before=preservation()
db=sqlite3.connect((L/'state/orders.sqlite3').as_uri()+'?mode=ro',uri=True)
events=[]
for ts,raw in db.execute('select ts,event from events where ts>=? order by id',(datetime.datetime(2026,10,8,tzinfo=ET).timestamp(),)):
 r=json.loads(raw)
 if inside(r.get('decision_ts',ts)) and r.get('asset') in ASSETS+('USDCAD',):r['_saved_ts']=ts;events.append(r)
orders=[json.loads(raw) for raw, in db.execute('select record from orders')];db.close()
(P/'events.json').write_text(json.dumps(events))
(P/'orders.json').write_text(json.dumps(orders))
(P/'status.json').write_text((L/'state/status.json').read_text())
groups=collections.defaultdict(list)
for r in events:groups[(day(r['decision_ts']),r['asset'],r.get('lane','cushion_z2'),r['decision_ts'])].append(r)
OWNED={'already_entered','already_paper_entered','position_filled','position_settled','zero_fill_locked','order_pending','order_rejected'}
def qualifies(r):
 try:
  if r.get('status') in OWNED:return False
  rule=lane_for(r);req=r.get('required_z',2 if r['asset']=='BTC' and r.get('lane')=='cushion_z2' and day(r['decision_ts'])<'2026-10-14' else rule.threshold(r['remaining_minutes']))
  return float(rule.floor)-1e-8<=r['signal_ask']<float(rule.ceiling)-1e-8 and r['signal_ask']-r['signal_bid']<=.02000001 and rule.min_minutes<=r['remaining_minutes']<=rule.max_minutes and r['z']>=req
 except (KeyError,ValueError,TypeError):return False
flat=[]
for key,observations in groups.items():
 qs=[r for r in observations if qualifies(r)]
 substantive=[r for r in observations if r.get('status') not in ('data_error','incomplete_spot','decision_candle_unavailable','no_decision_quote')]
 r=qs[0] if qs else (substantive[0] if substantive else observations[-1]);rule=lane_for(r)
 if qs:category='qualified'
 elif r['status']=='outside_window':category='outside_window'
 elif r['status']=='no_current_market':category='no_current_market'
 elif r['status'] in OWNED:category='already_owned'
 elif r['status']=='price_gate':
  ask=r['signal_ask'];bid=r['signal_bid']
  if ask<float(rule.floor)-1e-8:category='price_below_floor'
  elif ask>=float(rule.ceiling)-1e-8:category='price_above_ceiling'
  elif ask-bid>.02000001:category='spread_only'
  else:category='unexplained_price_gate'
 elif r['status']=='cushion_gate':category='cushion'
 else:category=r['status']
 flat.append(dict(day=key[0],asset=key[1],lane=key[2],decision_ts=key[3],category=category,record=r,observations=len(observations),recovered_error=bool(substantive) and any(x.get('status')=='data_error' for x in observations),qualified_records=qs))
def summarize(rs):
 cats=collections.Counter(x['category'] for x in rs);q=[x for x in rs if x['category']=='qualified'];fresh=sum(cats[k] for k in ('price_below_floor','price_above_ceiling','spread_only','cushion','qualified'))
 priced=[x['record'] for x in rs if x['record'].get('signal_ask') is not None and x['category'] not in ('already_owned','outside_window')]
 zrows=[x['record'] for x in rs if x['category'] in ('cushion','qualified')]
 ordermap={r['ticker']:r for r in orders}
 qmarkets=sorted({x['record']['ticker'] for x in q});qdetails=[]
 for ticker in qmarkets:
  signals=[x for x in q if x['record']['ticker']==ticker];o=ordermap.get(ticker,{})
  own_ts=o.get('created_ts',0);filled=float(o.get('ever_filled',o.get('filled',0)))>0
  allq=[r for x in signals for r in x['qualified_records']]
  qdetails.append(dict(ticker=ticker,asset=signals[0]['asset'],lanes=sorted({x['lane'] for x in signals}),qualified_minutes=len(signals),filled=filled,entry_in_window=inside(own_ts),entry_ts=own_ts if o else None,order_status=o.get('status'),execution_reasons=sorted({r.get('execution_status') or r.get('status') for r in allq})))
 return dict(scan_minutes=len(rs),categories=dict(cats),fresh_checks=fresh,price_passes=cats['cushion']+cats['qualified'],price_pass_pct=pct(cats['cushion']+cats['qualified'],fresh),qualified_lane_minutes=len(q),qualified_markets=len(qmarkets),qualified_market_lanes=len({(x['record']['ticker'],x['lane']) for x in q}),cushion_pass_pct=pct(cats['qualified'],cats['cushion']+cats['qualified']),recovered_errors=sum(x['recovered_error'] for x in rs),ask_median=median([r['signal_ask'] for r in priced]),z_median=median([r['z'] for r in zrows]),z_max=max([r['z'] for r in zrows],default=None),sigma_median=median([r['sigma'] for r in zrows if r.get('sigma') is not None]),abs_move_pct_median=median([100*abs(r['spot']/r['strike_proxy']-1) for r in zrows if r.get('strike_proxy')]),markets=qdetails)
daily={}
for d in DAYS:
 rs=[x for x in flat if x['day']==d and x['asset'] in ASSETS];stats=summarize(rs)
 filled=[r for r in orders if r.get('asset') in ASSETS and inside(r.get('created_ts',0)) and day(r['created_ts'])==d and float(r.get('ever_filled',r.get('filled',0)))>0]
 stats.update(filled_trades=len(filled),fills_by_asset=dict(collections.Counter(r['asset'] for r in filled)),expected_scans=1260,missing_scans=1260-len(rs));daily[d]=stats
byasset={d:{a:summarize([x for x in flat if x['day']==d and x['asset']==a]) for a in ASSETS+('USDCAD',)} for d in DAYS}
bylane={d:{l:summarize([x for x in flat if x['day']==d and x['lane']==l and x['asset'] in ASSETS]) for l in ('cushion_z2','premium_z3')} for d in DAYS}
pooled=summarize([x for x in flat if x['day'] in WEEKDAYS and x['asset'] in ASSETS])
pooled_assets={a:summarize([x for x in flat if x['day'] in WEEKDAYS and x['asset']==a]) for a in ASSETS}
btc_today=[x['record'] for x in flat if x['day']=='2026-10-14' and x['asset']=='BTC' and x['lane']=='cushion_z2' and x['category']=='cushion' and 2<=x['record']['z']<3]
output=dict(ts=time.time(),window='14:30 inclusive–16:00 exclusive America/New_York',assets=list(ASSETS),weekdays=WEEKDAYS,daily=daily,by_asset=byasset,by_lane=bylane,previous_weekday_pooled=pooled,previous_weekday_assets=pooled_assets,today_btc_z2_to_z3_rejections=btc_today,method='One asset/lane/minute; repeated checks deduplicated. First qualifying record preferred; otherwise first non-error record. First-failing filter categories. Actual historical thresholds. CAD separate. Same-hour fills by entry timestamp.',preservation_before=before)
after=preservation();assert {k:before[k] for k in ('hashes','flags','services')}=={k:after[k] for k in ('hashes','flags','services')}
output['preservation_after']=after
(P/'comparison.json').write_text(json.dumps(output,indent=2));(P/'classified.json').write_text(json.dumps(flat))
print(json.dumps(dict(daily={d:{k:v for k,v in s.items() if k!='markets'} for d,s in daily.items()},btc_removed_minutes=len(btc_today),btc_removed_markets=len({r['ticker'] for r in btc_today}),today_assets={a:{k:v for k,v in s.items() if k!='markets'} for a,s in byasset['2026-10-14'].items()}),indent=2))
