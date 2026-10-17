"""Build causal completed-minute metal features from archived public inputs."""
import gzip,json,math,pickle,hashlib,datetime as dt
from pathlib import Path
from collections import Counter,defaultdict
from decimal import Decimal as D,ROUND_HALF_UP
from zoneinfo import ZoneInfo
import numpy as np
from pull import P,DATA,ASSETS,START,END,ts
ET=ZoneInfo('America/New_York')
def save(n,x):(P/n).write_text(json.dumps(x,indent=2,allow_nan=False)+'\n')
def quote(c,field='c'):
 try:b,a=float(c['b'+field]),float(c['a'+field]);return (b,a) if 0<=b<=a<=1 and math.isfinite(b+a) else None
 except (TypeError,ValueError,KeyError):return None

def main():
 bars={a:{} for a in ASSETS};seconds={a:Counter() for a in ASSETS};conflicts=[]
 for p in sorted((DATA/'charts').glob('*.json.gz')):
  with gzip.open(p,'rt') as f:d=json.load(f)['response']['live_data']['details']
  a=d['asset'];seconds[a]['charts']+=1
  for x in d['timeseries']:
   t=int(x['t']);v=float(x['v']);seconds[a]['seconds']+=1
   if not math.isfinite(v) or v<=0:seconds[a]['invalid']+=1;continue
   if t%60000!=59000:continue
   minute=t//60000*60
   if minute in bars[a] and bars[a][minute]!=v:conflicts.append([a,minute,bars[a][minute],v])
   bars[a][minute]=v
 assert not conflicts,conflicts[:10]
 (P/'bars.pickle').write_bytes(pickle.dumps(bars))
 records=[];quality={a:Counter() for a in ASSETS};mismatches=[];all_outcomes={};months=defaultdict(Counter)
 for a in ASSETS:
  meta=json.loads((DATA/f'markets_{a}.json').read_text());cmap={r['ticker']:{int(c['t']):c for c in r['candles']} for r in map(json.loads,(DATA/f'candles_{a}.jsonl').read_text().splitlines())}
  fee=json.loads((DATA/f'series_{a}.json').read_text())['response']['series'];assert fee['fee_type']=='quadratic'
  vmap=bars[a];q=quality[a]
  for raw in meta:
   close,start=ts(raw['close_time']),ts(raw['open_time'])
   if not START<=close<END:continue
   q['markets']+=1
   if raw['result'] not in ('yes','no') or close-start!=900 or not raw.get('settlement_ts'):q['unsupported']+=1;continue
   ref=vmap.get(start-60);final=vmap.get(close-60);digits=int((raw.get('custom_strike') or {}).get('round_digits',{'EURUSD':5,'USDJPY':3,'GBPUSD':5,'AUDUSD':5,'USDCAD':5}[a]));tick=10**-digits
   quantum=D(1).scaleb(-digits)
   def rd(v):return D(str(v).replace(',','')).quantize(quantum,rounding=ROUND_HALF_UP)
   y=int(raw['result']=='yes');all_outcomes[raw['ticker']]=y
   q['yes']+=y;months[a][raw['close_time'][:7]]+=1
   if ref is not None and final is not None:
    q['rounded_ties']+=int(rd(final)==rd(ref));q['source_outcome_mismatch']+=int(int(rd(final)>=rd(ref))!=y)
   issues=[]
   if ref is not None and raw.get('floor_strike') is not None and rd(ref)!=rd(raw['floor_strike']):issues.append('reference')
   if raw.get('floor_strike') is None:q['missing_official_strike']+=1
   if final is not None and rd(final)!=rd(raw['expiration_value']):issues.append('final')
   if ref is not None and final is not None and int(rd(final)>=rd(ref))!=y:issues.append('outcome')
   if issues:mismatches.append(dict(ticker=raw['ticker'],issues=issues,reference=ref,final=final,strike=raw.get('floor_strike'),expiration=raw['expiration_value'],result=raw['result']))
   m=dict(ticker=raw['ticker'],asset=a,start=start,close=close,settled=ts(raw['settlement_ts']),result=y,ranges=raw['price_ranges'],multiplier=float(fee['fee_multiplier']),digits=digits,volume=float(raw.get('volume_fp',0)),features=[],candles=cmap.get(raw['ticker'],{}),issues=issues)
   assert m['settled']>=close
   for rem in range(14,0,-1):
    T=close-60*rem;pair=quote(m['candles'].get(T))
    if pair is None:q['missing_quote']+=1;continue
    keys=range(T-3660,T,60)
    if ref is None or any(k not in vmap for k in keys):q['missing_source']+=1;continue
    values=np.array([vmap[k] for k in keys]);logs=np.log(values);returns=np.diff(logs);sig=float(np.std(returns));s5=float(np.std(returns[-5:]));s15=float(np.std(returns[-15:]));smax=max(sig,s5,s15)
    if sig<=0:q['zero_sigma']+=1;continue
    spot=float(values[-1]);move=spot/ref-1;zy=move/(sig*math.sqrt(rem));zr=move/(smax*math.sqrt(rem));sigH=float(np.std(logs[rem:]-logs[:-rem]));zh=math.log(spot/ref)/max(sigH,sig*math.sqrt(rem));side=1 if sum(pair)>1 else -1
    hour=dt.datetime.fromtimestamp(T,ET).hour+dt.datetime.fromtimestamp(T,ET).minute/60
    fraczero=float(np.mean(returns==0));flat=0
    for val in values[-2::-1]:
     if val==spot:flat+=1
     else:break
    signedprev=[]
    for lag in (1,2):
     rr=np.array([vmap.get(k,np.nan) for k in range(T-(61+lag)*60,T-lag*60,60)])
     sp=float(np.std(np.diff(np.log(rr))))
     signedprev.append(side*(float(rr[-1])/ref-1)/(sp*math.sqrt(rem+lag)) if np.isfinite(sp) and sp>0 else -999.)
    recent=m['candles'].get(T,{});vol5=sum(float(m['candles'].get(T-j*60,{}).get('v') or 0) for j in range(5))
    f=dict(t=T,rem=rem,side=side,bid=pair[0] if side==1 else 1-pair[1],ask=pair[1] if side==1 else 1-pair[0],ybid=pair[0],yask=pair[1],z=side*zy,zy=zy,zrob=side*zr,zh=side*zh,zprev=min(signedprev),sigma=sig,sigma5=s5,sigma15=s15,volratio=s5/sig,rob_ratio=smax/sig,jump=float(np.max(np.abs(returns)))/sig,zero_fraction=fraczero,flat_minutes=flat,ref=ref,spot=spot,us=8<=hour<16,hour=hour,release=(8+25/60<=hour<=8+40/60 or 9+55/60<=hour<=10+10/60 or 13+55/60<=hour<=14+10/60),mom3=float(logs[-1]-logs[-4])/(sig*math.sqrt(3)),vol5=vol5,
           # Future values are labels only, never predictor inputs.
           residual=math.log(float(str(raw['expiration_value']).replace(',',''))/spot)/(sig*math.sqrt(rem)),threshold=math.log((float(rd(ref))-.5*tick)/spot)/(sig*math.sqrt(rem)))
    m['features'].append(f);q['features']+=1
   records.append(m)
  q['min_close']=min(m['close'] for m in records if m['asset']==a);q['max_close']=max(m['close'] for m in records if m['asset']==a)
 (P/'features.pickle').write_bytes(pickle.dumps(records))
 save('coverage.json',dict(quality=quality,source=seconds,mismatches=mismatches,months=months,cutoff=END,raw_response_receipt='data/pull_receipt.json',causal_feature_end='T-1 second; no interpolation; 61 complete minute endpoints'))
 print(json.dumps(quality));print('source mismatches',len(mismatches))
if __name__=='__main__':main()
