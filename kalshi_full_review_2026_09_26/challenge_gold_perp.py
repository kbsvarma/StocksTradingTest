"""Post-selection execution challenge, not an independent holdout or tuned new rule."""
import json,math,datetime,collections,random
from pathlib import Path
from census import get
from replay import ts
P=Path(__file__).resolve().parent;D=P/'perps_data';O=P/'results'
trades=json.loads((O/'KXGOLDPERP_reversion6h_perp_trades.json').read_text());fund=json.loads((D/'KXGOLDPERP_funding.json').read_text())['funding_rates'];byday=collections.defaultdict(dict)
def quote(c):
 try:b=float(c['bid']['close']);a=float(c['ask']['close'])
 except (KeyError,TypeError,ValueError):return None
 return (b,a) if 0<b<=a<100000 else None
# Fixed 60-second execution delay at both entry and scheduled exit.
for r in trades:
 T=int(r['ts'])-1
 for stamp in [T+60,T+6*3600+60]:
  day=datetime.datetime.fromtimestamp(stamp,datetime.timezone.utc).date().isoformat()
  if day in byday:continue
  path=D/('KXGOLDPERP_minute_'+day+'.json')
  if path.exists():res=json.loads(path.read_text())
  else:
   start=int(datetime.datetime.fromisoformat(day).replace(tzinfo=datetime.timezone.utc).timestamp());res=get('/margin/markets/KXGOLDPERP/candlesticks',{'start_ts':start,'end_ts':start+86400,'period_interval':1});path.write_text(json.dumps(res))
  byday[day]={x['end_period_ts']:x for x in res['candlesticks']}
rows=[];missing=0
for r in trades:
 T=int(r['ts'])-1;et=T+60;xt=T+21660
 def at(stamp):return quote(byday[datetime.datetime.fromtimestamp(stamp,datetime.timezone.utc).date().isoformat()].get(stamp,{}))
 e=at(et);x=at(xt)
 if not e or not x:missing+=1;continue
 side=r['side'];ep=e[1] if side==1 else e[0];xp=x[0] if side==1 else x[1];n=math.floor(50/ep);fees=n*(ep+xp)*.000072
 funding=sum(-side*n*float(f['mark_price'])*float(f['funding_rate']) for f in fund if et<ts(f['funding_time'])<=xt)
 net=side*n*(xp-ep)-fees+funding;stress=net-2*max(n*.0001,n*ep*.0001)
 rows.append({'ts':et,'net':net,'stress':stress,'day':r['day']})
eq=peak=dd=0
for r in rows:eq+=r['stress'];peak=max(peak,eq);dd=max(dd,peak-eq)
report={'post_selection_challenge':True,'rule':'Same 6-hour reversion signals; one minute delayed execution at both ends','trades':len(rows),'missing':missing,'net':sum(r['net'] for r in rows),'stress':sum(r['stress'] for r in rows),'max_drawdown':dd,'rows':rows}
(O/'gold_perp_execution_challenge.json').write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='rows'},indent=2))
