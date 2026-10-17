"""Score-state probability replay; public reads only, no trading interface."""
from pathlib import Path
import sys,math,json,gzip,collections,bisect
from functools import lru_cache
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P.parent/'kalshi_every_window_marketwide_2026_10_10'))
from core import read,qte,ts,pay,simulate,stats,fee,day
from recurring import vol
sys.path.insert(0,str(P.parent/'kalshi_high_frequency_2026_10_11'))
import public
public.P=P;public.RAW=P/'raw';public.RAW.mkdir(exist_ok=True)
ROOT=P.parent/'kalshi_broad_review_2026_10_10/replays'
def save(n,x):public.save(P/n,x)

class Model:
 def __init__(self,pa,pb):self.pa=pa;self.pb=pb
 @lru_cache(None)
 def game(self,a,b,s):
  q=self.pa if s==0 else 1-self.pb
  if max(a,b)>=4 and abs(a-b)>=2:return float(a>b)
  if min(a,b)>=3:
   d=q*q/(q*q+(1-q)**2)
   return d if a==b else q+(1-q)*d if a>b else q*d
  return q*self.game(a+1,b,s)+(1-q)*self.game(a,b+1,s)
 @lru_cache(None)
 def tb(self,a,b,first):
  if max(a,b)>=7 and abs(a-b)>=2:return float(a>b)
  n=a+b;s=first if n==0 or ((n-1)//2)%2==1 else 1-first
  q=self.pa if s==0 else 1-self.pb
  if min(a,b)>=6:
   x=self.pa*(1-self.pb);y=(1-self.pa)*self.pb;d=x/(x+y)
   return d if a==b else q+(1-q)*d if a>b else q*d
  return q*self.tb(a+1,b,first)+(1-q)*self.tb(a,b+1,first)
 @lru_cache(None)
 def setdist(self,a,b,s):
  if max(a,b)>=6 and abs(a-b)>=2:
   out=[0.]*4;out[s if a>b else 2+s]=1.;return tuple(out)
  if a==6 and b==6:
   p=self.tb(0,0,s);out=[0.]*4;out[1-s]=p;out[3-s]=1-p;return tuple(out)
  q=self.game(0,0,s);x=self.setdist(a+1,b,1-s);y=self.setdist(a,b+1,1-s)
  return tuple(q*u+(1-q)*v for u,v in zip(x,y))
 @lru_cache(None)
 def match(self,a,b,s):
  if a>=2:return 1.
  if b>=2:return 0.
  d=self.setdist(0,0,s)
  return sum(d[i]*self.match(a+1,b,i) for i in (0,1))+sum(d[2+i]*self.match(a,b+1,i) for i in (0,1))
 def current(self,sa,sb,ga,gb,pa,pb,s):
  if sa>=2:return 1.
  if sb>=2:return 0.
  q=self.game(pa,pb,s);x=self.setdist(ga+1,gb,1-s);y=self.setdist(ga,gb+1,1-s);d=[q*u+(1-q)*v for u,v in zip(x,y)]
  return sum(d[i]*self.match(sa+1,sb,i) for i in (0,1))+sum(d[2+i]*self.match(sa,sb+1,i) for i in (0,1))
 def clear(self):
  # Method caches include self; clear between fitted models to bound memory.
  for f in (self.game,self.tb,self.setdist,self.match):f.cache_clear()

def fit(base,target):
 lo,hi=-.12,.12
 for _ in range(15):
  d=(lo+hi)/2;m=Model(base+d,base-d);p=(m.match(0,0,0)+m.match(0,0,1))/2;m.clear()
  if p<target:lo=d
  else:hi=d
 d=(lo+hi)/2;return Model(base+d,base-d)

def collect():
 out=P/'tennis_plays';out.mkdir(exist_ok=True)
 for asset in ('KXWTAMATCH','KXATPMATCH'):
  d=ROOT/asset
  for mp in sorted(d.glob('*_milestones.json')):
   e=mp.name.removesuffix('_milestones.json');dest=out/(e+'.json.gz')
   if dest.exists() or (d/(e+'_plays.json.gz')).exists():continue
   miles=[r for r in read(mp) if r.get('details',{}).get('main_game_event_ticker')==e]
   if len(miles)!=1 or str(miles[0]['details'].get('best_of'))!='3':continue
   try:save('tennis_plays/'+e+'.json.gz',public.get('/live_data/milestone/'+miles[0]['id']+'/game_stats'))
   except Exception as ex:save('tennis_plays/'+e+'_error.json',{'error':str(ex)})
  print('COLLECT',asset,flush=True)

def run(adaptive=0,date_low='2026-09-26',date_high='2026-10-09',periods=None,only=None,output_prefix=None):
 if periods is None:periods=[('selection','2026-09-26','2026-10-02'),('later','2026-10-03','2026-10-09')]
 candidates=collections.defaultdict(list);qc=collections.Counter();coverage=[]
 for asset in ('KXWTAMATCH','KXATPMATCH'):
  d=ROOT/asset;meta=read(d/'series.json');groups=collections.defaultdict(list)
  for m in read(d/'markets.json'):groups[m['event_ticker']].append(m)
  for event,ms in sorted(groups.items()):
   mp=d/(event+'_milestones.json');cp=d/(event+'_candles.json.gz');pp=d/(event+'_plays.json.gz')
   if not pp.exists():pp=P/'tennis_plays'/(event+'.json.gz')
   if not all(x.exists() for x in (mp,cp,pp)):qc['missing_files']+=1;continue
   miles=[r for r in read(mp) if r.get('details',{}).get('main_game_event_ticker')==event]
   if len(miles)!=1 or str(miles[0]['details'].get('best_of'))!='3':qc['format']+=1;continue
   start=ts(miles[0]['start_date']);date=day(start)
   if not date_low<=date<=date_high:continue
   cm={k:{int(c['end_period_ts']):c for c in v} for k,v in read(cp).items()};plays=[]
   for per in read(pp).get('pbp',{}).get('periods',[]):
    for ev in per.get('events',[]):
     if ev.get('wall_clock'):plays.append({**ev,'home':per.get('home_competitor_id'),'away':per.get('away_competitor_id')})
   plays.sort(key=lambda x:(int(x['wall_clock']),x.get('event_type')=='game_won'))
   if not plays:qc['no_plays']+=1;continue
   home,away=plays[0]['home'],plays[0]['away'];byid={m.get('custom_strike',{}).get('tennis_competitor'):m for m in ms}
   if home not in byid or away not in byid:qc['competitor_mapping']+=1;continue
   mh=byid[home];cs=cm.get(mh['ticker'],{});pre=None;pt=None
   for mins in (30,5):
    pt=(start-mins*60)//60*60;q=qte(cs.get(pt))
    if q and q[1]-q[0]<=.1 and .02<sum(q)/2<.98:pre=sum(q)/2;break
   if pre is None or pt>=int(plays[0]['wall_clock']):qc['prematch_quote']+=1;continue
   model=fit(.65 if asset=='KXATPMATCH' else .60,pre);base_pa,base_pb=model.pa,model.pb;pts=[int(e['wall_clock']) for e in plays];times=sorted(t for t in cs if start<=t<min(ts(m['close_time']) for m in ms))
   cumulative=[];totals=[0,0,0,0];counted=set()
   for ev in plays:
    ident=(ev['wall_clock'],ev.get('server'),ev.get('result'))
    if ident not in counted and ev.get('event_type') in ('point','game_won') and ev.get('server') in (home,away) and ev.get('result') in ('server_won','receiver_won'):
     counted.add(ident);idx=0 if ev['server']==home else 2;totals[idx]+=ev['result']=='server_won';totals[idx+1]+=1
    cumulative.append(tuple(totals))
   coverage.append(dict(event=event,date=date,asset=asset,points=len(plays),prematch_t=pt,prematch_prob=pre,pa=model.pa,pb=model.pb))
   for lag in (30,90):
    for t in times:
     i=bisect.bisect_right(pts,t-lag)-1
     if i<0:continue
     ev=plays[i]
     if ev.get('event_type')!='point' or t-pts[i]>120 or (ev['home'],ev['away'])!=(home,away):continue
     try:
      sa,sb,ga,gb=[int(ev[k]) for k in ('home_sets','away_sets','home_games','away_games')];sc={0:0,15:1,30:2,40:3,50:4};a=sc[int(ev['home_score'])];b=sc[int(ev['away_score'])];s=[home,away].index(ev['server'])
     except (KeyError,ValueError,TypeError):continue
     if sa not in (0,1) or sb not in (0,1) or ga>6 or gb>6 or ga==gb==6 or (max(ga,gb)>=6 and abs(ga-gb)>=2) or max(a,b)>=4 and abs(a-b)>=2:continue
     if adaptive:
      wh,nh,wa,na=cumulative[i];pa2=round((adaptive*base_pa+wh)/(adaptive+nh),3);pb2=round((adaptive*base_pb+wa)/(adaptive+na),3)
      if (model.pa,model.pb)!=(pa2,pb2):model.clear();model=Model(pa2,pb2)
     prob=model.current(sa,sb,ga,gb,a,b,s)
     opts=[]
     for player,m in ((home,byid[home]),(away,byid[away])):
      cs2=cm.get(m['ticker'],{})
      if vol(cs2,t)<5:continue
      for side in ('yes','no'):
       q=qte(cs2.get(t),side)
       if not q or not .10<=q[1]<=.90 or q[1]-q[0]>.05000001:continue
       p=prob if (player==home)==(side=='yes') else 1-prob
       opts.append((m,cs2,side,q,p))
     for blend in ((only[0],) if only else (0.,.5)):
      ranked=[]
      for m,cc,side,q,p in opts:
       pred=(1-blend)*p+blend*sum(q)/2;edge=pred-q[1]-fee(q[1],1,float(meta['fee_multiplier']))-.01
       ranked.append((edge,m['ticker'],side,m,cc,q,p,pred))
      if not ranked:continue
      edge,_,side,m,cc,q,p,pred=max(ranked,key=lambda x:x[:3])
      for cut in ((only[1],) if only else (.02,.04,.06)):
       if edge+1e-10<cut:continue
       for delay in (False,True):
        qq=qte(cc.get(t+60),side,'close' if delay else 'open');entered=t+(60 if delay else 3)
        if not qq or max(q[1],qq[1])>min(.92,q[1]+.02)+1e-8 or entered>=min(ts(m['close_time']),ts(m['settlement_ts'])):continue
        payout=pay(m,side)
        if payout is None:continue
        key=f'blend{blend}/edge{cut}/lag{lag}/'+('delay60' if delay else 'base')
        candidates[key].append(dict(owner=event,event=event,asset=asset,date=day(entered),match_date=date,ticker=m['ticker'],side=side,t=t,entered=entered,entry=max(q[1],qq[1]),cap=min(.92,q[1]+.02),mult=float(meta['fee_multiplier']),payout=payout,settled=ts(m['settlement_ts']),close=ts(m['close_time']),source_t=pts[i],prematch_t=pt,model_prob=p,prediction=pred,edge=edge,score=[sa,sb,ga,gb,a,b,s],serve_counts=cumulative[i],serve_prob=[model.pa,model.pb],prior_strength=adaptive))
   model.clear()
   if len(coverage)%30==0:print('MODELED',len(coverage),flush=True)
 results={};ledgers={};frozen=[]
 for key,rr in candidates.items():
  for period,lo,hi in periods:
   for cost in (.01,.02):
    rows,sk=simulate([r for r in rr if lo<=r['date']<=hi and (period!='selection' or r['settled']<ts('2026-10-03T04:00:00Z'))],1,cost);res=stats(rows);days=(ts(hi+'T00:00:00Z')-ts(lo+'T00:00:00Z'))/86400+1;res.update(trades_per_day=len(rows)/days,skips=sk);k=key+'/'+period+f'/cost{int(cost*100)}';results[k]=res;ledgers[k]=rows
 for b in (0.,.5):
  for cut in (.02,.04,.06):
   pref=f'blend{b}/edge{cut}'
   keys=[pref+'/lag30/base/selection/cost1',pref+'/lag30/base/selection/cost2',pref+'/lag30/delay60/selection/cost1',pref+'/lag90/base/selection/cost1']
   if all(results.get(k,{}).get('net',-1)>0 and results[k]['trades_per_day']>=5 for k in keys):frozen.append(pref)
 pref=output_prefix or (f'tennis_adaptive{adaptive}_' if adaptive else 'tennis_')
 save(pref+'candidates.json.gz',candidates);save(pref+'results.json',results);save(pref+'ledgers.json.gz',ledgers);save(pref+'coverage.json',coverage);save(pref+'quality.json',dict(qc));save(pref+'selection.json',dict(passing=frozen,selected=max(frozen,key=lambda k:results[k+'/lag30/base/selection/cost1']['net']) if frozen else None))
 for k,v in results.items():
  if '/lag30/base/' in k and k.endswith('cost1'):print(k,v['trades'],v['net'],v['drawdown'],flush=True)
if __name__=='__main__':collect();run(int(sys.argv[1]) if len(sys.argv)>1 else 0)
