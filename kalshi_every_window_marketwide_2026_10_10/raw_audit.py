"""Independently verify finalist selections against original quote payloads."""
from common import *
from decimal import Decimal as D
from collections import Counter
from datetime import timezone
counts=Counter();errors=[]
def ok(x,label):
 counts['assertions']+=1
 if not x:errors.append(label)
def quotes(c,side):
 if not c:return None
 try:
  nums=[]
  for f in ['yes_bid','yes_ask']:
   z=c[f];v=z.get('close_dollars',z.get('close'));v=D(v)/100 if isinstance(v,int) else D(str(v));nums.append(v)
  b,a=nums
  if not 0<=b<=a<=1:return None
  return (b,a) if side=='yes' else (1-a,1-b)
 except (KeyError,TypeError,ValueError):return None
def value(m,side):
 if m['result'] in ('yes','no'):return D(int(m['result']==side))
 v=D(m['settlement_value_dollars']);return v if side=='yes' else 1-v
root=P/'daily_extension';index={g['owner']:g for g in read(root/'daily_index.json')};candidates=read(root/'daily_candidates.json.gz')
for key in ['KXAAAGASD/time1/favorite/base','KXHIGHNY/time0/favorite/base']:
 for r in candidates[key]:
  g=index[r['owner']];t=r['t'];expected=g['times'][1 if g['family']=='gas' else 0];ok(t==expected,r['ticker']+' checkpoint');cm=read(root/'daily_quotes'/(str(t)+'.json.gz'));options=[]
  for m in g['markets']:
   if not ts(m['open_time'])<=t<ts(m['close_time']):continue
   cs={c['end_period_ts']:c for c in cm.get(m['ticker'],[])}
   for side in ['yes','no']:
    q=quotes(cs.get(t),side)
    if q:options.append({'m':m,'ticker':m['ticker'],'side':side,'mid':sum(q)/2,'ask':q[1],'bid':q[0],'cs':cs})
  yes=[x for x in options if x['side']=='yes']
  if g['family']=='temperature':pick=max(yes,key=lambda x:(x['mid'],x['ticker']))
  else:
   atm=min(yes,key=lambda x:(abs(x['mid']-D('.5')),x['ticker']));pick=max([x for x in options if x['ticker']==atm['ticker']],key=lambda x:(x['mid'],x['side']))
  ok((pick['ticker'],pick['side'])==(r['ticker'],r['side']),r['ticker']+' outcome choice')
  ok(abs(float(pick['ask'])-r['ask'])<1e-8,r['ticker']+' ask')
  ok(abs(float(value(pick['m'],r['side']))-r['payout'])<1e-8,r['ticker']+' payout')
  ok(ts(pick['m']['settlement_ts'])==r['settled'],r['ticker']+' settlement')
  counts['daily_selected_rows']+=1
# Independently reconstruct rolling WNBA calibration samples from raw decisions.
root=P/'wnba_extension';index=read(root/'pregame_index.json');samples=[];snaps={}
for g in index:
 t=(g['start']-1800)//60*60;cm=read(root/'pregame'/g['asset']/(g['event']+'_quotes.json.gz'));options=[]
 for m in g['markets']:
  cs={c['end_period_ts']:c for c in cm.get(m['ticker'],[])};q=quotes(cs.get(t),'yes')
  if q:options.append({'m':m,'ticker':m['ticker'],'p':sum(q)/2,'ask':q[1],'bid':q[0]})
 if len(options)!=len(g['markets']):continue
 fav=max(options,key=lambda x:(x['p'],x['ticker']));samples.append({'t':t,'p':fav['p'],'y':value(fav['m'],'yes'),'settled':max(ts(m['settlement_ts']) for m in g['markets'])});snaps[g['event']]=(g,fav)
for r in read(root/'pregame_candidates.json.gz')['KXWNBAGAME/pre30/calibrated/base']:
 g,fav=snaps[r['event']];b=r['calibration_boundary'];binid=min(3,int(max(D(0),fav['p']-D('.4'))/D('.15')))
 # Floating-point bin thresholds in the simulation are checked separately by
 # matching bin identities before comparing fitted residual; differences fail.
 tr=[x for x in samples if b-28*86400<=x['t']<b and x['settled']<b and min(3,int(max(0,float(x['p'])-.4)/.15))==min(3,int(max(0,float(fav['p'])-.4)/.15))]
 prob=max(D('.001'),min(D('.999'),fav['p']+sum((x['y']-x['p'] for x in tr),D(0))/(len(tr)+40)))
 ok(len(tr)==r['calibration_n'],r['ticker']+' calibration count');ok(abs(float(prob)-r['calibrated_favorite_probability'])<1e-8,r['ticker']+' calibration probability');ok(all(x['settled']<b<=r['t'] for x in tr),r['ticker']+' calibration time');counts['wnba_selected_rows']+=1
out={'passed':not errors,'counts':dict(counts),'errors':errors[:50],'limitations':'Validates archived snapshot selection and payout, not original source arrival or executable depth.'};save(P/'raw_audit.json',out);print(json.dumps(out,indent=2));assert not errors
