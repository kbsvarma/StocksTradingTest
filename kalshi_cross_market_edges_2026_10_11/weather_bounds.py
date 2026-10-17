"""External weather observations vs daily-high contracts; NOT source arbitrage."""
from pathlib import Path
import sys,json,csv,io,requests,time,datetime as dt,collections,math,gzip
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
P=Path(__file__).resolve().parent;OLD=P.parent/'kalshi_every_window_marketwide_2026_10_10';sys.path.insert(0,str(OLD))
from core import read,qte,ts,pay,simulate,stats,fee
from recurring import vol
sys.path.insert(0,str(P.parent/'kalshi_high_frequency_2026_10_11'))
import public
public.P=P;public.RAW=P/'raw';public.RAW.mkdir(exist_ok=True)
STATIONS={'KXHIGHNY':('NYC','America/New_York'),'KXHIGHCHI':('MDW','America/Chicago'),'KXHIGHMIA':('MIA','America/New_York'),'KXHIGHLAX':('LAX','America/Los_Angeles'),'KXHIGHPHIL':('PHL','America/New_York'),'KXHIGHTATL':('ATL','America/New_York'),'KXHIGHTDC':('DCA','America/New_York'),'KXHIGHTPHX':('PHX','America/Phoenix')}
def save(n,x):
 if n.endswith('.gz'):
  with gzip.open(P/n,'wt') as f:json.dump(x,f,allow_nan=False)
 else:(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def fetch():
 d=P/'metar';d.mkdir(exist_ok=True)
 for station,_ in STATIONS.values():
  p=d/(station+'.csv')
  if p.exists():continue
  args=dict(station=station,data='tmpf',year1=2026,month1=8,day1=15,year2=2026,month2=10,day2=11,tz='Etc/UTC',format='onlycomma',latlon='no',elev='no',missing='M',trace='T',direct='no',report_type=[3,4]);r=requests.get('https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py',params=args,timeout=45);r.raise_for_status();assert r.text.startswith('station,valid,tmpf');p.write_text(r.text);save('metar/'+station+'_receipt.json',dict(url=r.url,retrieved=time.time(),bytes=len(r.content)));print('METAR',station,len(r.text),flush=True)
def source():
 out={}
 for asset,(station,zone) in STATIONS.items():
  days=collections.defaultdict(list);z=ZoneInfo(zone)
  for r in csv.DictReader((P/'metar'/(station+'.csv')).open()):
   try:t=int(dt.datetime.strptime(r['valid'],'%Y-%m-%d %H:%M').replace(tzinfo=dt.timezone.utc).timestamp());v=float(r['tmpf'])
   except (ValueError,KeyError):continue
   local=dt.datetime.fromtimestamp(t,z)
   if 6<=local.hour<=23:days[local.date().isoformat()].append((t,v))
  out[asset]={day:sorted(rr) for day,rr in days.items()}
 return out
def side_for_bound(m,bound):
 kind=m.get('strike_type');lo=m.get('floor_strike');hi=m.get('cap_strike')
 if kind=='greater' and lo is not None and bound>float(lo):return 'yes'
 if kind=='greater_or_equal' and lo is not None and bound>=float(lo):return 'yes'
 if kind in ('between','less','less_or_equal') and hi is not None and bound>float(hi):return 'no'
 return None
def intraday_collect():
 index=[g for g in read(OLD/'daily_index.json') if g['asset'] in STATIONS]
 (P/'weather_intraday').mkdir(exist_ok=True)
 def one(g):
  dest=P/'weather_intraday'/(g['event']+'.json.gz')
  if dest.exists():return
  date=dt.datetime.fromisoformat(g['date']).replace(tzinfo=ZoneInfo(STATIONS[g['asset']][1]));lo=int(date.replace(hour=8,minute=50).timestamp());hi=int(date.replace(hour=20,minute=1).timestamp())
  x=public.get('/markets/candlesticks',dict(market_tickers=','.join(m['ticker'] for m in g['markets']),start_ts=lo,end_ts=hi,period_interval=1));save('weather_intraday/'+g['event']+'.json.gz',{m['market_ticker']:m['candlesticks'] for m in x.get('markets',[])})
 with ThreadPoolExecutor(max_workers=4) as ex:
  for i,_ in enumerate(ex.map(one,index)):
   if i%50==0:print('INTRADAY_FETCH',i,len(index),flush=True)
def run(intraday=False):
 index=[g for g in read(OLD/'daily_index.json') if g['asset'] in STATIONS];src=source();cand=collections.defaultdict(list);quality=[];counts=collections.Counter()
 for g in index:
  allobs=src[g['asset']].get(g['date'],[]);vals=[v for _,v in allobs];fvals={float(m['expiration_value']) for m in g['markets'] if m.get('expiration_value') not in ('',None)}
  quality.append(dict(asset=g['asset'],date=g['date'],station=STATIONS[g['asset']][0],observations=len(vals),observed_max=max(vals) if vals else None,settlement_values=sorted(fvals),max_minus_final=max(vals)-next(iter(fvals)) if vals and len(fvals)==1 else None))
  if intraday:
   date=dt.datetime.fromisoformat(g['date']).replace(tzinfo=ZoneInfo(STATIONS[g['asset']][1]));times=range(int(date.replace(hour=9).timestamp()),int(date.replace(hour=20).timestamp())+1,300);cm_all={k:{int(c['end_period_ts']):c for c in v} for k,v in read(P/'weather_intraday'/(g['event']+'.json.gz')).items()}
  else:times=g['times']
  for lag,buffer in [(10,2),(30,2),(10,3),(30,3)]:
   key=f'lag{lag}/buffer{buffer}'
   for t in times:
    obs=[(s,v) for s,v in allobs if s<=t-lag*60]
    if not obs:counts[key+'_source_missing']+=1;continue
    highest=max(v for s,v in obs);bound=highest-buffer;file=OLD/'daily_quotes'/(str(t)+'.json.gz')
    if not intraday and not file.exists():continue
    cm=cm_all if intraday else {k:{int(c['end_period_ts']):c for c in v} for k,v in read(file).items()};options=[]
    for m in g['markets']:
     side=side_for_bound(m,bound);cs=cm.get(m['ticker'],{})
     if side is None:continue
     counts[key+'_bound_matched']+=1
     q=qte(cs.get(t),side)
     if not q:counts[key+'_no_quote']+=1;continue
     if not .01<=q[1]<.98:counts[key+'_price']+=1;continue
     if q[1]-q[0]>.05+1e-8:counts[key+'_spread']+=1;continue
     if vol(cs,t)<5:counts[key+'_volume']+=1;continue
     cost=q[1]+fee(q[1],1,g['mult'])+.01
     if cost>.995:counts[key+'_cost']+=1;continue
     options.append((cost,m['ticker'],side,m,cs,q))
    if not options:continue
    _,_,side,m,cs,q=min(options,key=lambda x:x[:3])
    for delay in [False,True]:
     qq=qte(cs.get(t+60),side,'close' if delay else 'open');when=t+(60 if delay else 3)
     if not qq or max(q[1],qq[1])>min(.98,q[1]+.02)+1e-8 or when>=min(ts(m['close_time']),ts(m['settlement_ts'])):continue
     r=dict(owner=g['owner'],event=g['event'],asset=g['asset'],date=g['date'],ticker=m['ticker'],side=side,t=t,entered=when,settled=ts(m['settlement_ts']),close=ts(m['close_time']),entry=max(q[1],qq[1]),cap=min(.98,q[1]+.02),mult=g['mult'],payout=pay(m,side),source_last=obs[-1][0],observed_max=highest,bound=bound,lag_minutes=lag,buffer=buffer,settlement_final=m.get('expiration_value'))
     cand[key+('/delay60' if delay else '/base')].append(r)
 res={};led={}
 for key,rr in cand.items():
  for co,lo,hi,days in [('earlier','2026-08-15','2026-09-11',28),('later','2026-09-12','2026-10-09',28),('october','2026-10-01','2026-10-09',9),('all','2026-08-15','2026-10-09',56)]:
   for cost in [.01,.02]:
    rows,sk=simulate([r for r in rr if lo<=r['date']<=hi],1,cost);s=stats(rows);s.update(trades_per_day=len(rows)/days,skips=sk);k=key+'/'+co+f'/cost{int(cost*100)}';res[k]=s;led[k]=rows
 pref='weather_intraday_' if intraday else 'weather_'
 save(pref+'source_audit.json',quality);save(pref+'candidates.json.gz',cand);save(pref+'results.json',res);save(pref+'ledgers.json.gz',led);save(pref+'skips.json',dict(counts))
 for k,v in res.items():
  if '/base/' in k and k.endswith('/cost1'):print(k,v['trades'],v['net'],v['trades_per_day'],flush=True)
if __name__=='__main__':
 fetch()
 if 'intraday' in sys.argv:intraday_collect();run(True)
 else:run()
