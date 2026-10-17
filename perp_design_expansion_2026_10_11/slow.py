from study import *
def slow_signals(t,ts,A,h,family,mult,target):
 mid=pd.Series(A[:,:2].mean(axis=1),index=ts);q=mid[mid.index%3600==0];lp=np.log(q);vol=lp.diff().rolling(72,min_periods=65).std(ddof=0).shift(1)*np.sqrt(24);mean=lp.rolling(h,min_periods=h).mean().shift(1);sd=lp.rolling(h,min_periods=h).std(ddof=0).shift(1)
 if family=='breakout':side=pd.Series(np.where(lp>lp.rolling(h).max().shift(1),1,np.where(lp<lp.rolling(h).min().shift(1),-1,0)),index=lp.index)
 if family=='momentum':ret=lp-lp.shift(h);side=np.sign(ret).where(ret.abs()>vol,0)
 if family=='pullback':z=(lp-mean)/sd;trend=np.sign(lp.shift(1)-lp.shift(73));side=-np.sign(z).where((z.abs()>=1)&(-np.sign(z)==trend),0)
 S=np.zeros((len(ts),6));crypto=r.old.M[t]['asset_class']=='Crypto'
 for stamp,s in side.dropna().items():
  if not s:continue
  v=vol.get(stamp,np.nan)
  if not np.isfinite(v) or v<=0:continue
  stop=max(.005 if crypto else .002,mult*v)
  if stop>.12:continue
  if not crypto:
   d=pd.Timestamp(stamp,unit='s',tz='UTC').tz_convert('America/New_York')
   if d.weekday()>4 or not 570<=d.hour*60+d.minute<=900:continue
  i=np.searchsorted(ts,stamp);px=mid.iloc[i];tp=px*(1+s*3*stop) if target==3 else px*(100 if s>0 else .001)
  S[i]=[s,stop,tp,10080,60,0]
 return S
if __name__=='__main__':
 out=[];cover=json.load(open(P/'coverage.json'))
 for c in cover:
  t=c['ticker'];ts,A,F,_=data_load(t)
  for family in ['breakout','momentum','pullback']:
   for h in [12,24,72]:
    for mult in [1,2]:
     for target in [3,0]:
      S=slow_signals(t,ts,A,h,family,mult,target)
      for mode in [0,2]:
       for period in ['early','later']:
        for scenario in SCEN:
         v=execute(t,ts,A,F,S,period,mode,scenario)
         if v:v.update(family=family,horizon=h,stop_mult=mult,target_R=target);out.append(v)
  print(t,'done',flush=True);pd.DataFrame(out).to_csv(P/'slow_results.csv',index=False)
