import json,math,datetime,urllib.request,urllib.parse,time,statistics,collections
from pathlib import Path
P=Path(__file__).resolve().parent;D=P.parent/'kalshi_perps_expanded_2026_09_26/data'
def ts(s):return int(datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
fund=json.loads((D/'KXBTCPERP_funding.json').read_text())['funding_rates'];fund=sorted(fund,key=lambda f:f['funding_time']);candles=json.loads((D/'KXBTCPERP_candles.json').read_text())['candlesticks'];c={x['end_period_ts']:x for x in candles}
start=min(c);end=max(c);days=(end-start)/86400
out={'period_start':datetime.datetime.fromtimestamp(start,datetime.timezone.utc).isoformat(),'period_end':datetime.datetime.fromtimestamp(end,datetime.timezone.utc).isoformat(),'days':days,'funding_count':len(fund),'positive':sum(float(f['funding_rate'])>0 for f in fund),'negative':sum(float(f['funding_rate'])<0 for f in fund),'zero':sum(float(f['funding_rate'])==0 for f in fund),'sum_rates':sum(float(f['funding_rate']) for f in fund)}
out['simple_annualized']=out['sum_rates']*365/days
weekly=[]
for k in range(7):
 lo=start+k*7*86400;hi=min(lo+7*86400,end);rates=[float(f['funding_rate']) for f in fund if lo<ts(f['funding_time'])<=hi]
 if hi>lo:weekly.append({'start':lo,'end':hi,'count':len(rates),'sum_rates':sum(rates),'annualized':sum(rates)*365/((hi-lo)/86400)})
out['weeks']=weekly
bars={}
for s in range(start-3600,end,290*3600):
 f=P/'data'/f'btc_spot_{s}.json'
 if f.exists():r=json.loads(f.read_text())
 else:
  url='https://api.exchange.coinbase.com/products/BTC-USD/candles?'+urllib.parse.urlencode({'granularity':3600,'start':s,'end':min(s+290*3600,end)})
  for attempt in range(4):
   try:
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'research/1.0'}),timeout=30) as response:r=json.load(response)
    break
   except Exception:
    if attempt==3:raise
    time.sleep(2)
  f.write_text(json.dumps(r))
 bars.update({int(x[0])+3600:float(x[4]) for x in r})
def q(x):
 try:b=float(x['bid']['close']);a=float(x['ask']['close'])
 except (ValueError,TypeError,KeyError):return None
 return (b,a) if 0<b<=a<100 else None
common=sorted(t for t in c if t in bars and q(c[t]));first,last=common[0],common[-1];entry=q(c[first]);exit=q(c[last]);spotfirst=bars[first]*.0001;spotlast=bars[last]*.0001
# Fixed units hedge: each short perp matched by 0.0001 BTC spot, never assume price delta disappears across venues.
rows=[]
for n in [1,5]:
 funding=n*sum(float(f['mark_price'])*float(f['funding_rate']) for f in fund if first<ts(f['funding_time'])<=last)
 for execution in ['taker','maker_assumed']:
  ep=entry[0] if execution=='taker' else entry[1];xp=exit[1] if execution=='taker' else exit[0];fee=.0012 if execution=='taker' else .0002
  basis=n*((ep-xp)+(spotlast-spotfirst));perpfees=n*(ep+xp)*fee
  for spotfee_bp in [0,10,40,60,120]:
   sf=n*(spotfirst+spotlast)*spotfee_bp/10000;net=funding+basis-perpfees-sf
   rows.append({'contracts':n,'execution':execution,'spot_fee_bp_per_side':spotfee_bp,'spot_btc_units':n*.0001,'entry_spot_dollars':n*spotfirst,'entry_perp_notional':n*ep,'capital_1x_perp_plus_spot':n*(ep+spotfirst),'funding':funding,'basis_and_crossing_pnl':basis,'perpfees':perpfees,'spotfees':sf,'net_before_spot_spread_and_other_costs':net})
out['hedge_entry_ts']=first;out['hedge_exit_ts']=last;out['hedge_scenarios']=rows
basisseries=[(sum(q(c[t]))/2-bars[t]*.0001)/(bars[t]*.0001)*1e4 for t in common];out['historical_basis_proxy_bp']={'observations':len(common),'median':statistics.median(basisseries),'min':min(basisseries),'max':max(basisseries),'p90_abs':sorted(abs(x) for x in basisseries)[int(.9*len(basisseries))]}
# Collateral excursion: approximate short liability from executable ask changes; excludes exchange-specific maintenance formula.
funding_unit=lambda t:sum(float(f['mark_price'])*float(f['funding_rate']) for f in fund if first<ts(f['funding_time'])<=t)
out['short_leg_worst_unrealized_5contracts']=min(5*(entry[0]-q(c[t])[1])+5*funding_unit(t) for t in common)
out['btc_spot_max_move_from_entry']=max(bars[t]/bars[first]-1 for t in common)
(P/'results.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
