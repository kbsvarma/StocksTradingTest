from pathlib import Path
import numpy as np,pandas as pd
from numba import njit
P=Path(__file__).resolve().parent;OLD=P.parent/'cover_phase3_2026_09';ROOT=P.parent
@njit(cache=True)
def run(ev,op,cl,roll,atr,mult,tick,fee,slip,fraction,spacing,control,eff,threshold):
 n=len(cl);q=0.;original=0.;anchor=0.;unitatr=0.;last=0.;due=-1.;net=fees=0.;path=np.zeros(n);quant=np.zeros(n);turn=0.
 for i in range(n):
  en,ep,xn,xp,rq=ev[i]
  if not np.isfinite(cl[i]):path[i]=net;quant[i]=q;continue
  # A scheduled trim/restoration executes at next open only while original trade survives.
  if q and not roll[i] and due>=0:
   change=due-q;net+=q*(op[i]-last)*mult;cost=abs(change)*(fee/2+slip*tick*mult);net-=cost;fees+=cost;q=due;last=op[i];turn+=abs(change)
  due=-1.
  if en>0:
   assert q==0
   original=en;anchor=ep;unitatr=atr[max(0,i-1)];q=en*(fraction if control else 1.);q=np.rint(q)
   if xn>0:net+=q*(xp-ep)*mult-fee*q;fees+=fee*q;turn+=2*q;q=0.
   else:net+=q*(cl[i]-ep)*mult-fee/2*q;fees+=fee/2*q;turn+=q
  elif q:
   if xn>0:net+=q*(xp-last)*mult-fee/2*q;fees+=fee/2*q;turn+=q;q=0.
   else:net+=q*(cl[i]-last)*mult
  # Virtual trade ending clears state even if integer sizing left actual qty zero.
  if xn>0:original=0.
  if original and rq>0 and not control and fraction<1 and np.isfinite(unitatr) and unitatr>0:
   # Countercyclical target: fully restore at entry, progressively trim above it.
   trim=np.floor(max(0.,cl[i]-anchor)/(spacing*unitatr));floor=np.ceil(original*fraction)
   target=max(floor,original-trim);target=min(original,target)
   if not np.isfinite(eff[i]) or eff[i]>threshold:target=original
   if target!=q:due=target
  last=cl[i];path[i]=net;quant[i]=q
 if q:cost=q*(fee/2+slip*tick*mult);path[-1]-=cost;fees+=cost;turn+=q
 return path,quant,fees,turn

def main():
 rows=[];paths={}
 for parent in ['baseline','ridge10','width20']:
  for case,fee,slip in [('stress',7.,1),('severe',14.,2)]:
   evs=pd.read_parquet(OLD/f'core_events_{parent}_{case}.parquet');books={};sizes={}
   for sym,mult,tick in [('NQ',2.,.25),('ES',5.,.25),('YM',.5,1.)]:
    raw=pd.read_parquet(ROOT/f'trend_bot/research/enterprise_2026_09/native/{sym}.parquet').reindex(evs.index);tr=pd.concat([raw.high-raw.low,(raw.high-raw.close.shift()).abs(),(raw.low-raw.close.shift()).abs()],axis=1).max(axis=1).rolling(80).mean();ev=evs[sym][['entry_qty','entry_px','exit_qty','exit_px','ref_qty']].fillna(0).to_numpy()
    clean=raw.close.diff().where(~raw.roll.fillna(False).astype(bool),0);eff=(clean.rolling(20).sum().abs()/clean.abs().rolling(20).sum()).to_numpy()
    for name,fraction,spacing,control,threshold in [('parent',1.,1.,False,1.),('fixed_one_third',1/3,1,True,1.),('fixed_two_thirds',2/3,1,True,1.)]+[(f'gate{gate}_harvest_{f:.3f}_{step}',f,step,False,gate) for gate in [.3,.5] for f in [1/3,2/3] for step in [.5,1.,2.]]:
     p,q,fees,turn=run(ev,raw.open.to_numpy(),raw.close.to_numpy(),raw.roll.fillna(False).to_numpy(dtype=bool),tr.to_numpy(),mult,tick,fee,slip,fraction,spacing,control,eff,threshold);books.setdefault(name,[]).append(pd.Series(p,index=evs.index));sizes.setdefault(name,[]).append(pd.Series(q,index=evs.index))
   for name,ps in books.items():
    nav=sum(ps);qty=sum(sizes[name]);d=nav.diff().fillna(nav.iloc[0]).groupby(nav.index.normalize()).sum();paths[f'{parent}_{case}_{name}']=d
    for period,lo,hi in [('all','2011','2027'),('early','2011','2021'),('middle','2021','2025'),('recent','2025','2027')]:
     x=d[(d.index>=lo)&(d.index<hi)];eq=x.cumsum();rows.append(dict(parent=parent,case=case,design=name,period=period,net=x.sum(),dd=(eq-eq.cummax().clip(lower=0)).min(),sharpe=x.mean()/x.std()*np.sqrt(252),mean_contracts=qty[(qty.index>=lo)&(qty.index<hi)].mean()))
   print(parent,case,flush=True)
 pd.DataFrame(rows).to_csv(P/'futures_gated_inventory_scores.csv',index=False);pd.DataFrame(paths).to_parquet(P/'futures_gated_inventory_daily.parquet')
if __name__=='__main__':main()
