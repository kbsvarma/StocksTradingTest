from rotation import *
rows=[]
for source in ['proxy','venue','transfer']:
 for stress in [False,True]:
  C,O,F=transfer_build(stress) if source=='transfer' else old.build(source,stress);src='proxy' if source=='transfer' else source;lp=np.log(C);vol=lp.diff().rolling(20).std().shift(1)
  for name,score in [('weekly_top2',lp-lp.shift(7)),('risk_adjusted',(lp-lp.shift(7))/(vol*np.sqrt(7)))]:
   rank=score.rank(axis=1,ascending=False,method='first');top=(rank<=2).astype(float)*2
   for weekday in range(7):
    w=top.where(pd.DataFrame(np.tile((C.index.weekday==weekday)[:,None],(1,4)),index=C.index,columns=C.columns)).ffill().fillna(0);S=w.where(lp.shift(30).notna(),0).shift(1).fillna(0).to_numpy()
    for fs in [0.,1.]:
     for per,st,en in periods(C,src):
      if per not in ['all','later']:continue
      r,path,pnl=old.simulate(C,O,F,S,src,stress,st,en,list(range(4)),fs);r.update(source=source,scenario='combined' if stress else 'base',funding_enabled=bool(fs),period=per,design=name,weekday=weekday);rows.append(r)
      if weekday==0:np.savez_compressed(P/'ledgers'/f'phase_{source}_{name}_{fs}_{per}_{stress}.npz',equity=path,asset_pnl=pnl)
 print(source,flush=True)
pd.DataFrame(rows).to_csv(P/'phase_results.csv',index=False)
