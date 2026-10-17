from rotation import *
rows=[]
for stress in [False,True]:
 C,O,F=old.build('proxy',stress)
 for j,a in enumerate(old.ASSETS):
  factor=1e-6/float(old.prior.r.old.M['KX'+a+'PERP']['contract_size']);C.iloc[:,j]*=factor;O.iloc[:,2*j:2*j+2]*=factor;F.iloc[:,j]*=factor
 cc,oo,ff=transfer_build(stress);C=pd.concat([C,cc],axis=1);O=pd.concat([O,oo],axis=1);F=pd.concat([F,ff],axis=1);lp=np.log(C);vol=lp.diff().rolling(20).std().shift(1);valid=lp.shift(30).notna()
 ss={'always_long':valid.astype(float).shift(1).fillna(0).to_numpy(),'LT30':(lp>lp.shift(30)).where(valid,False).astype(float).shift(1).fillna(0).to_numpy()}
 for name,score in [('weekly_top_half',lp-lp.shift(7)),('risk_adjusted_half',(lp-lp.shift(7))/(vol*np.sqrt(7)))]:
  rank=score.rank(axis=1,ascending=False,method='first');w=(rank<=4).astype(float)*2;w=w.where(pd.DataFrame(np.tile((C.index.weekday==0)[:,None],(1,8)),index=C.index,columns=C.columns)).ffill().fillna(0);ss[name]=w.where(valid,0).shift(1).fillna(0).to_numpy()
 for name,S in ss.items():
  for fs in [0.,1.]:
   for per,st,en in periods(C,'proxy'):
    r,path,pnl=old.simulate(C,O,F,S,'proxy',stress,st,en,list(range(8)),fs);r.update(design=name,scenario='combined' if stress else 'base',funding_enabled=bool(fs),period=per,asset_pnl=pnl.tolist());rows.append(r)
    if per=='all':np.savez_compressed(P/'ledgers'/f'eight_{name}_{fs}_{stress}.npz',equity=path,asset_pnl=pnl)
pd.DataFrame(rows).to_csv(P/'eight_results.csv',index=False);print('Eight-asset check complete')
