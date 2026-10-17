from rotation import *
rows=[]
for stress in [False,True]:
 C,O,F=old.build('proxy',stress)
 for j,a in enumerate(old.ASSETS):
  factor=1e-6/float(old.prior.r.old.M['KX'+a+'PERP']['contract_size']);C.iloc[:,j]*=factor;O.iloc[:,2*j:2*j+2]*=factor;F.iloc[:,j]*=factor
 cc,oo,ff=transfer_build(stress);C=pd.concat([C,cc],axis=1);O=pd.concat([O,oo],axis=1);F=pd.concat([F,ff],axis=1)
 for omit in range(8):
  chosen=[j for j in range(8) if j!=omit];lp=np.log(C.copy());lp.iloc[:,omit]=np.nan;valid=lp.shift(30).notna();vol=lp.diff().rolling(20).std().shift(1);ss={'always_long':valid.astype(float).shift(1).fillna(0).to_numpy()}
  for name,score in [('weekly_top_half',lp-lp.shift(7)),('risk_adjusted_half',(lp-lp.shift(7))/(vol*np.sqrt(7)))]:
   rank=score.rank(axis=1,ascending=False,method='first');w=(rank<=4).astype(float)*7/4;w=w.where(pd.DataFrame(np.tile((C.index.weekday==0)[:,None],(1,8)),index=C.index,columns=C.columns)).ffill().fillna(0);ss[name]=w.where(valid,0).shift(1).fillna(0).to_numpy()
  for name,S in ss.items():
   for fs in [0.,1.]:
    r,_,_=old.simulate(C,O,F,S,'proxy',stress,0,len(C),chosen,fs);r.update(design=name,scenario='combined' if stress else 'base',funding_enabled=bool(fs),omit=C.columns[omit]);rows.append(r)
pd.DataFrame(rows).to_csv(P/'eight_omissions.csv',index=False)
print('Completed8asset leave-one-out; top4of7 retained, no optimization of count')
