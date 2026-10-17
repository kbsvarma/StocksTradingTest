"""Every-game liquidity-entry screen, explicit unverified maker-fill proxy."""
from pathlib import Path
import sys,json,math,collections,gzip,argparse
HERE=Path(__file__).resolve().parent
OLD=HERE.parent/'kalshi_every_window_marketwide_2026_10_10'
sys.path.insert(0,str(OLD))
from core import read,qte,ts,pay,simulate,stats
from recurring import vol
def write(name,x):
 p=HERE/name
 if p.suffix=='.gz':
  with gzip.open(p,'wt') as f:json.dump(x,f,allow_nan=False)
 else:p.write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def extreme(c,side):
 o=c.get('price',{});key='low' if side=='yes' else 'high';v=o.get(key+'_dollars',o.get(key))
 try:p=float(v)/100 if isinstance(v,int) else float(v)
 except (TypeError,ValueError):return None
 return p if side=='yes' else 1-p
def candidates():
 out=collections.defaultdict(list);den=collections.Counter();rejected=collections.Counter();index=read(OLD/'pregame_index.json')
 for g in index:
  co='selection' if '2026-09-12'<=g['date']<='2026-09-30' else 'later' if '2026-10-01'<=g['date']<='2026-10-09' else 'earlier'
  den[co]+=1;t=(g['start']-600)//60*60;p=OLD/'pregame'/g['asset']/(g['event']+'_quotes.json.gz')
  if not p.exists():rejected['no_file']+=1;continue
  cm={k:{int(c['end_period_ts']):c for c in v} for k,v in read(p).items()}
  oo=[]
  for m in g['markets']:
   cs=cm.get(m['ticker'],{});q=qte(cs.get(t))
   if q:oo.append((sum(q)/2,m['ticker'],m,cs))
  if len(oo)!=len(g['markets']):rejected['missing_signal_quote']+=1;continue
  _,_,m,cs=max(oo,key=lambda x:(x[0],x[1]))
  for side in ['yes','no']:
   q=qte(cs.get(t),side)
   if not q or not .01<=q[1]<=.99 or q[1]-q[0]>.05000001 or vol(cs,t)<5:rejected['price_spread_volume']+=1;continue
   base=dict(asset=g['asset'],owner=g['owner'],event=g['event'],date=g['date'],cohort=co,ticker=m['ticker'],side=side,t=t,close=ts(m['close_time']),settled=ts(m['settlement_ts']),payout=pay(m,side),mult=g['mult'],original_mult=g['mult'],ask=q[1],bid=q[0],scheduled_start=g['start'])
   for discount in [0,.01,.02,.03]:
    for stress,through,passive in [('base',.01,True),('through2c',.02,True),('no_maker',.01,False)]:
     key=f'{side}/discount{discount:.2f}/{stress}';r=None;limit=round(math.floor((q[1]-discount+1e-10)*100)/100,2)
     if discount and passive:
      for end in range(t+120,t+301,60):
       c=cs.get(end,{});pr=extreme(c,side);v=float(c.get('volume_fp') or c.get('volume') or 0)
       if limit>=.01 and pr is not None and pr<=limit-through+1e-8 and v>=5:
        r={**base,'entered':end,'entry':limit,'cap':limit,'mult':g['mult']*.25,'fill_type':'unverified_maker_proxy','through_price':pr,'through_cents':through*100,'maker_limit':limit};break
     if r is None:
      at=t+300 if discount else t;qa=qte(cs.get(at),side);qb=qte(cs.get(at+60),side,'open');cap=min(.99,q[1]+.02)
      if qa and qb and .01<=qa[1]<=.99 and qa[1]-qa[0]<=.05000001 and max(qa[1],qb[1])<=cap+1e-8:
       r={**base,'entered':at+3,'entry':round(max(qa[1],qb[1]),4),'cap':cap,'fill_type':'taker_fallback' if discount else 'immediate_taker'}
     if r and r['entered']<min(r['scheduled_start'],r['settled'],r['close']):out[key].append(r)
 return dict(out),dict(den),dict(rejected)
def main(mode):
 if mode=='select':
  cand,den,rej=candidates();write('sports_candidates.json.gz',cand);write('sports_input_summary.json',dict(denominator=den,rejected=rej))
 else:cand=read(HERE/'sports_candidates.json.gz');den=read(HERE/'sports_input_summary.json')['denominator']
 cos=['selection'] if mode=='select' else ['earlier','later'];results={};ledgers={}
 for key,items in cand.items():
  for co in cos:
   for cost in [.01,.02]:
    rr=[r for r in items if r['cohort']==co];tr,sk=simulate(rr,1,cost);st=stats(tr);st['windows']=den[co];st['risk_skips']=dict(sk);st['calendar_days']={'selection':19,'later':9,'earlier':28}[co];st['trades_per_calendar_day']=len(tr)/st['calendar_days'];st['unverified_maker_fills']=sum(r['fill_type']=='unverified_maker_proxy' for r in tr);k=key+'/'+co+'/cost'+str(round(cost*100));results[k]=st;ledgers[k]=tr
 write('sports_'+mode+'_results.json',results);write('sports_'+mode+'_ledgers.json.gz',ledgers)
 if mode=='select':
  names=sorted(set(k.rsplit('/',1)[0] for k in cand));eligible=[]
  for name in names:
   kk=[name+'/base/selection/cost1',name+'/base/selection/cost2',name+'/through2c/selection/cost1']
   if all(results[k]['net']>0 and results[k]['trades_per_calendar_day']>=20 for k in kk):eligible.append(name)
  eligible.sort(key=lambda n:results[n+'/base/selection/cost1']['net'],reverse=True)
  write('sports_selection_frozen.json',dict(chosen=eligible[0] if eligible else None,passing=eligible,rule='positive base,2c andstrict2c-through plus>=20/day; rank selectionnet',trials=8))
 for k,v in results.items():
  if '/base/' in k and k.endswith('cost1'):print(k,{z:v[z] for z in ['trades','net','drawdown','trades_per_calendar_day','unverified_maker_fills']},flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['select','validate']);main(p.parse_args().mode)
