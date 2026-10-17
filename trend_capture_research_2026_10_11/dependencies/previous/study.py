from pathlib import Path
import sys,json,gzip
import numpy as np,pandas as pd
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P/'dependencies'/'maker'))
import research as r
def data_load(t):
 if t!='KXBTCPERP':
  if (P/'extra_data'/(t+'_1m.json.gz')).exists():
   orig=r.D;r.D=P/'extra_data'
   try:return r.load(t)
   finally:r.D=orig
  return r.load(t)
 import importlib.util
 spec=importlib.util.spec_from_file_location('btc_frozen_reference',r.P/'inputs'/'btc_previous'/'study.py');b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
 return b.ts.copy(),np.c_[b.A,b.df['vol'].fillna(0).to_numpy()],b.fc.copy(),b
SCEN={'base':(1,1,0,0,0),'cost5':(5,1,0,0,0),'delay2':(1,2,0,0,0),'closecross':(1,1,1,0,0),'combined':(5,2,1,1,1)}
def signals(t,ts,A,horizon,family):
 mid=pd.Series((A[:,0]+A[:,1])/2,index=ts);m=mid[mid.index%300==0];lp=np.log(m);w=horizon//5
 vol=lp.diff().rolling(48,min_periods=43).std(ddof=0).shift(1)*np.sqrt(12);mean=lp.rolling(w,min_periods=w).mean().shift(1);sd=lp.rolling(w,min_periods=w).std(ddof=0).shift(1);z=(lp-mean)/sd
 if family=='momentum':q=lp-lp.shift(w);side=np.sign(q).where(q.abs()>vol,0)
 if family=='breakout':side=pd.Series(np.where(lp>lp.rolling(w).max().shift(1),1,np.where(lp<lp.rolling(w).min().shift(1),-1,0)),index=lp.index)
 if family=='overshoot':side=-np.sign(z).where(z.abs()>=2,0)
 if family=='trendpullback':
  trend=np.sign(lp.shift(1)-lp.shift(73));side=-np.sign(z).where((z.abs()>=1)&(-np.sign(z)==trend),0)
 S=np.zeros((len(ts),6));crypto=r.old.M[t]['asset_class']=='Crypto'
 for stamp,s in side.dropna().items():
  if s==0:continue
  i=np.searchsorted(ts,stamp);v=vol.get(stamp,np.nan);px=mid.iloc[i]
  if not np.isfinite(v) or v<=0:continue
  stop=max(.005 if crypto else .002,2*v)
  if stop>.04:continue
  costs=(A[i,1]-A[i,0])/px+r.fees['maker_fee_rates'][t]+r.fees['taker_fee_rates'][t]+.0002
  if 2*stop<4*costs:continue
  age=720
  if not crypto:
   d=pd.Timestamp(stamp,unit='s',tz='UTC').tz_convert('America/New_York');minute=d.hour*60+d.minute
   if d.weekday()>4 or not 570<=minute<=900:continue
   age=min(360,955-minute)
  S[i]=[s,stop,px*(1+s*2*stop),age,15,0]
 return S

def execute(t,ts,A,F,S,period,mode,scenario,save=None):
 split='2026-09-01' if r.old.M[t]['asset_class']=='Crypto' else '2026-10-01';s=int(pd.Timestamp(split,tz='UTC').timestamp());end=min(len(ts),np.searchsorted(ts,int(pd.Timestamp('2026-10-10',tz='UTC').timestamp())))
 st=0 if period=='early' else np.searchsorted(ts,s);en=min(end,np.searchsorted(ts,s)) if period=='early' else end
 while en>st and not np.isfinite(A[en-1,0]+A[en-1,1]):en-=1
 if en<=st:return None
 fr,de,cc,miss,adv=SCEN[scenario];tick=float(r.old.M[t]['tick_size']);tick=max(tick,.0005) if t=='KXSILVERPERP' else tick
 o,path,dd,attempts,expired,amb,missing,held=r.replay(A,S,F,st,en,mode,5,fr/1e4,de,cc,miss,r.fees['taker_fee_rates'][t],r.fees['maker_fee_rates'][t],tick,100,0,adv)
 assert abs(path[-1]-100-o[:,12].sum())<1e-7
 if save:np.savez_compressed(save,ledger=o,path=np.c_[ts[st:en],path])
 days=(ts[en-1]-ts[st])/86400
 return dict(ticker=t,period=period,mode=mode,scenario=scenario,trades=len(o),net=float(o[:,12].sum()),dd=dd,gross=float(o[:,8].sum()),fees=float(o[:,10].sum()),funding=float(o[:,9].sum()),friction=float(o[:,11].sum()),without_best3=float(o[:,12].sum()-np.sort(o[:,12])[-3:].sum()),days=days,net_per_30days=float(o[:,12].sum())*30/max(days,1),missing_held=missing,attempts=attempts)
if __name__=='__main__':
 results=[];coverage=[]
 for t in r.old.M:
  f=r.D/(t+'_1m.json.gz')
  if t!='KXBTCPERP' and (not f.exists() or not json.load(gzip.open(f,'rt'))['candlesticks']):continue
  ts,A,F,_=data_load(t);coverage.append(dict(ticker=t,start=int(ts[0]),end=int(ts[-1]),valid=int(np.isfinite(A[:,0]+A[:,1]).sum())))
  for family in ['momentum','breakout','overshoot','trendpullback']:
   for h in [30,120,360]:
    S=signals(t,ts,A,h,family)
    for mode in [0,2]:
     for period in ['early','later']:
      for scen in SCEN:
       v=execute(t,ts,A,F,S,period,mode,scen)
       if v:v.update(family=family,horizon=h);results.append(v)
  print(t,'done',flush=True);pd.DataFrame(results).to_csv(P/'results.csv',index=False)
 (P/'coverage.json').write_text(json.dumps(coverage,indent=2))
 x=pd.DataFrame(results);e=x[x.period=='early'];keys=['ticker','family','horizon','mode'];p=e.pivot(index=keys,columns='scenario',values='net_per_30days');c=e[e.scenario=='base'].set_index(keys).trades;p['score']=p.min(axis=1);eligible=p[(p.score>0)&(c>=20)].reset_index().sort_values('score',ascending=False);leaders=eligible.groupby('ticker').head(1)
 leaders.to_csv(P/'frozen_early_leaders.csv',index=False);x.merge(leaders[keys],on=keys).to_csv(P/'leader_details.csv',index=False);print('LEADERS',leaders.to_string(index=False),flush=True)
