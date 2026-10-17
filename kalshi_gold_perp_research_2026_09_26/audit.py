import json,math,datetime,collections,statistics
from pathlib import Path
P=Path(__file__).resolve().parent;OLD=P.parent/'kalshi_full_review_2026_09_26';D=P/'data'
def load(p):return json.loads(p.read_text())
def ts(s):return int(datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
def q(c):
 try:b=float(c['bid']['close']);a=float(c['ask']['close'])
 except (KeyError,TypeError,ValueError):return None
 return (b,a,(b+a)/2) if 0<b<=a<100 else None
hours={c['end_period_ts']:c for c in load(D/'hourly.json')['candlesticks']}
minutes={}
for f in sorted((OLD/'perps_data').glob('KXGOLDPERP_minute_*.json')):minutes.update({c['end_period_ts']:c for c in load(f)['candlesticks']})
for f in sorted(D.glob('minute_*.json')):minutes.update({c['end_period_ts']:c for c in load(f)['candlesticks']})
fund=[(ts(f['funding_time']),float(f['funding_rate']),float(f['mark_price'])) for f in load(D/'funding.json')['funding_rates']]
END=1790380800

def run(offset=0,delay=60,bp=1,control=None,end=END,start=0):
 rows=[];skip=collections.Counter();equity=peak=dd=0;missing_marks=0
 for T,c in sorted(hours.items()):
  if T%21600!=offset*3600 or T<start or T+21600>end:continue
  prev=q(hours.get(T-21600,{}));cur=q(c)
  if not prev or not cur:skip['signal_missing']+=1;continue
  if (cur[1]-cur[0])/cur[2]>.002 or float(c.get('volume') or 0)<=0:skip['signal_wide_inactive']+=1;continue
  side=-(1 if cur[2]>prev[2] else -1 if cur[2]<prev[2] else 0)
  if control:side=control
  if not side:continue
  et=T+delay;xt=T+21600+delay;e=q(minutes.get(et,{}));x=q(minutes.get(xt,{}))
  if not e or not x:skip['execution_missing']+=1;continue
  ep=e[1] if side==1 else e[0];xp=x[0] if side==1 else x[1];n=math.floor(50/ep)
  if not n or not .95<ep/cur[2]<1.05:skip['size_or_gap']+=1;continue
  fees=n*(ep+xp)*.000072;funding=sum(-side*n*m*r for ft,r,m in fund if et<ft<=xt)
  stresscost=2*max(n*.0001,n*ep*bp/10000)
  gross=side*n*(xp-ep);net=gross-fees+funding-stresscost
  worst=0
  for st in range(et,xt+1,60):
   quote=q(minutes.get(st,{}))
   if not quote:missing_marks+=1;continue
   mark=quote[0] if side==1 else quote[1]
   ff=sum(-side*n*m*r for ft,r,m in fund if et<ft<=st)
   pnl=side*n*(mark-ep)-n*(ep+mark)*.000072+ff-stresscost
   worst=min(worst,pnl);eq=equity+pnl;peak=max(peak,eq);dd=max(dd,peak-eq)
  equity+=net
  day=datetime.datetime.fromtimestamp(T,datetime.timezone.utc)
  rows.append({'signal':T,'entry_ts':et,'exit_ts':xt,'day':day.date().isoformat(),'weekday':day.weekday(),'side':side,'count':n,'entry':ep,'exit':xp,'gross':gross,'fees':fees,'funding':funding,'stress_cost':stresscost,'net':net,'worst_open_pnl':worst})
 by=collections.defaultdict(float)
 for r in rows:by[r['day']]+=r['net']
 vals=sorted([r['net'] for r in rows],reverse=True);total=sum(vals)
 return {'offset':offset,'delay':delay,'extra_bp_each_leg':bp,'control':control,'trades':len(rows),'days':len(by),'net':total,'gross':sum(r['gross'] for r in rows),'fees':sum(r['fees'] for r in rows),'funding':sum(r['funding'] for r in rows),'wins':sum(r['net']>0 for r in rows),'minute_close_drawdown':dd,'worst_open_trade':min([r['worst_open_pnl'] for r in rows],default=0),'long_net':sum(r['net'] for r in rows if r['side']==1),'short_net':sum(r['net'] for r in rows if r['side']==-1),'weekday_net':sum(r['net'] for r in rows if r['weekday']<5),'weekend_net':sum(r['net'] for r in rows if r['weekday']>=5),'without_best_trade':total-sum(vals[:1]),'without_best_3_trades':total-sum(vals[:3]),'without_best_day':total-max(by.values(),default=0),'daily':dict(by),'skips':dict(skip),'missing_minute_marks':missing_marks,'rows':rows}
base=run();out={'base':base,'offsets':[run(offset=i) for i in range(6)],'delays':[run(delay=d) for d in (60,300,900)],'costs':[run(bp=b) for b in (1,3,5,10)],'controls':[run(control=c) for c in (1,-1)],'extension':run(start=END,end=max(hours)-900),'coverage':{'first_hour':min(hours),'last_hour':max(hours),'hourly_count':len(hours),'minute_count':len(minutes),'funding_count':len(fund)}}
(P/'audit_results.json').write_text(json.dumps(out,indent=2))
for k,v in out.items():
 if k=='coverage':print(k,v);continue
 for row in v if isinstance(v,list) else [v]:print(k,json.dumps({a:b for a,b in row.items() if a not in ['rows','daily']}))
