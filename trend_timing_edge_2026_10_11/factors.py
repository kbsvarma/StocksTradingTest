from rotation import *
rows=[]
for source in ['proxy','venue','transfer']:
 for stress in [False,True]:
  C,O,F=transfer_build(stress) if source=='transfer' else old.build(source,stress);lp=np.log(C);vol=lp.diff().rolling(20).std().shift(1);ret=lp-lp.shift(7);src='proxy' if source=='transfer' else source
  for name,score in [('risk_adjusted',ret/(vol*np.sqrt(7))),('volatility_only',vol)]:
   rank=score.rank(axis=1,ascending=False,method='first');top=(rank<=2).astype(float)*2;w=top.where(pd.DataFrame(np.tile((C.index.weekday==0)[:,None],(1,4)),index=C.index,columns=C.columns)).ffill().fillna(0);S=w.where(lp.shift(30).notna(),0).shift(1).fillna(0).to_numpy()
   for fs in [0.,1.]:
    for per,st,en in periods(C,src):
     r,_,_=old.simulate(C,O,F,S,src,stress,st,en,list(range(4)),fs);r.update(source=source,scenario='combined' if stress else 'base',funding_enabled=bool(fs),period=per,design=name);rows.append(r)
 print(source,flush=True)
pd.DataFrame(rows).to_csv(P/'factor_results.csv',index=False)
