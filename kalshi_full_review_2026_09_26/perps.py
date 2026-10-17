import json,math,datetime,statistics,collections,random
from pathlib import Path
from census import get
from replay import ts
P=Path(__file__).resolve().parent;D=P/'perps_data';D.mkdir(exist_ok=True);O=P/'results'
START=1786147200;END=1790380800
M=json.loads((P/'data/perps_markets.json').read_text())['markets']
def getcached(t,kind):
 path=D/(t+'_'+kind+'.json')
 if path.exists():return json.loads(path.read_text())
 r=get('/margin/markets/'+t+'/candlesticks',{'start_ts':START,'end_ts':END,'period_interval':60}) if kind=='candles' else get('/margin/funding_rates/historical',{'ticker':t,'start_ts':START,'end_ts':END})
 path.write_text(json.dumps(r));return r
def quote(c,phase):
 try:b=float(c['bid'][phase]);a=float(c['ask'][phase])
 except (ValueError,KeyError,TypeError):return None
 if not 0<b<=a<100000:return None
 return b,a,(a+b)/2
results=[]
for m in M:
 t=m['ticker'];raw=getcached(t,'candles');fr=getcached(t,'funding');cs={c['end_period_ts']:c for c in raw['candlesticks']};fund=[(ts(x['funding_time']),float(x['funding_rate']),float(x['mark_price'])) for x in fr.get('funding_rates',[])]
 rate=.000072 if m['asset_class']=='Metals' else .0012;tick=float(m['tick_size'])
 for name,lookback,hold,direction in [('momentum6h',6,6,1),('reversion6h',6,6,-1),('momentum24h',24,24,1)]:
  trades=[];skips=collections.Counter()
  for T,c in sorted(cs.items()):
   if T%(hold*3600):continue
   prior=quote(cs.get(T-lookback*3600,{}),'close');cur=quote(c,'close');entry=quote(cs.get(T+3600,{}),'open');exitq=quote(cs.get(T+hold*3600,{}),'close')
   if not all([prior,cur,entry,exitq]):skips['missing_quote']+=1;continue
   if (cur[1]-cur[0])/cur[2]>.002 or float(c.get('volume') or 0)<=0:skips['wide_or_inactive_signal']+=1;continue
   side=(1 if cur[2]>prior[2] else -1 if cur[2]<prior[2] else 0)*direction
   if not side:skips['flat']+=1;continue
   ep=entry[1] if side==1 else entry[0];xp=exitq[0] if side==1 else exitq[1]
   if not .95<ep/cur[2]<1.05:skips['entry_gap_over_5pct']+=1;continue
   count=math.floor(50/ep)
   if count<1:skips['minimum_contract_over_budget']+=1;continue
   cost=count*(ep+xp)*rate;funding=sum(-side*count*mark*f for ft,f,mark in fund if T+1<ft<=T+hold*3600)
   net=side*count*(xp-ep)-cost+funding;stress=net-2*max(count*tick,count*ep*.0001)
   trades.append({'ts':T+1,'day':datetime.datetime.fromtimestamp(T,datetime.timezone.utc).date().isoformat(),'side':side,'count':count,'entry':ep,'exit':xp,'fees':cost,'funding':funding,'net':net,'stress':stress})
  n=len(trades);days=sorted({r['day'] for r in trades});equity=peak=dd=0
  for r in trades:equity+=r['stress'];peak=max(peak,equity);dd=max(dd,peak-equity)
  med=days[len(days)//2] if days else '';by=collections.defaultdict(list)
  for r in trades:by[r['day']].append(r['stress'])
  draws=[];rng=random.Random(992)
  for _ in range(5000 if n else 0):
   ds=[rng.choice(days) for __ in days];draws.append(sum(sum(by[d]) for d in ds)/sum(len(by[d]) for d in ds))
  draws.sort()
  row={'ticker':t,'rule':name,'available_hourly_candles':len(cs),'first_candle':min(cs) if cs else None,'funding_records':len(fund),'current_taker_fee_rate':rate,'trades':n,'days':len(days),'net':round(sum(r['net'] for r in trades),4),'stress':round(sum(r['stress'] for r in trades),4),'fees':round(sum(r['fees'] for r in trades),4),'funding':round(sum(r['funding'] for r in trades),4),'max_drawdown_stress':round(dd,4),'first_half_stress':round(sum(r['stress'] for r in trades if r['day']<med),4),'second_half_stress':round(sum(r['stress'] for r in trades if r['day']>=med),4),'bootstrap_p05_per_trade':round(draws[250],5) if draws else None,'skips':dict(skips),'historical_depth_verified':False}
  results.append(row);(O/(t+'_'+name+'_perp_trades.json')).write_text(json.dumps(trades))
 print(t,'candles',len(cs),'done',flush=True)
(O/'perps_results.json').write_text(json.dumps(results,indent=2));print('COMPLETE',len(results),flush=True)
