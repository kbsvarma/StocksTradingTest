from older_test import *
if __name__=='__main__':
 raw=json.load(gzip.open(P/'older_zec_hourly.json.gz','rt'));d=pd.DataFrame(raw['rows'],columns=['t','low','high','open','close','volume']).set_index('t').sort_index();ts=np.arange(d.index.min(),d.index.max()+3600,3600);d=d.reindex(ts);A=d[['open','high','low','close','volume']].to_numpy();A[:,:4]*=.01
 lp=np.log(d.close);v=lp.diff().rolling(72,min_periods=65).std(ddof=0).shift(1)*np.sqrt(24);ret=lp-lp.shift(12);side=np.sign(ret).where(ret.abs()>v,0);stop=np.maximum(.005,2*v);side=side.where(stop<=.12,0);ema=lp.ewm(span=168,min_periods=168,adjust=False).mean().shift(1);align=np.sign(lp-ema);eff=(lp-lp.shift(72)).abs()/lp.diff().abs().rolling(72).sum();mean=lp.rolling(24).mean().shift(1);sd=lp.rolling(24).std(ddof=0).shift(1);z=(lp-mean)/sd;rows=[]
 for name in ['original','aligned','efficient','both','hybrid']:
  s=side.copy();target=d.close*.01*(1+s*3*stop)
  if name in ['aligned','both']:s=s.where(s==align,0)
  if name in ['efficient','both']:s=s.where(eff>=.2,0)
  if name=='hybrid':
   range_side=-np.sign(z).where(z.abs()>=2,0);s=s.where(eff>=.2,range_side);target=target.where(eff>=.2,np.exp(mean)*.01)
  s=s.where(stop<=.12,0)
  S=np.c_[s.fillna(0),stop,target];S[~np.isfinite(S).all(axis=1),0]=0
  for period in ['2022','2023','2024','2025','all']:
   st=0 if period=='all' else np.searchsorted(ts,int(pd.Timestamp(period+'-01-01',tz='UTC').timestamp()));en=len(ts) if period=='all' else np.searchsorted(ts,int(pd.Timestamp(str(int(period)+1)+'-01-01',tz='UTC').timestamp()))
   for scenario,fr,de,fund in [('base',.0001,0,.0001),('combined',.0005,1,.0003)]:
    o,dd,cash=run(A,S,ts,st,en,fr,de,fund);rows.append(dict(rule=name,period=period,scenario=scenario,trades=len(o),net=float(o[:,10].sum()),dd=dd))
 pd.DataFrame(rows).to_csv(P/'regime_results.csv',index=False);print(pd.DataFrame(rows).to_string(index=False))
