from pathlib import Path
import json,gzip
import numpy as np,pandas as pd
from numba import njit
P=Path(__file__).resolve().parent
@njit
def run(A,S,ts,st,en,fr,delay,fundrate):
 cash=100.;pos=0;side=0;qty=0;ep=0.;ei=0;stop=0.;target=0.;pending=0;due=0;cool=st;peak=100.;dd=0.;n=0;out=np.zeros((en-st+1,12))
 for i in range(st,en):
  valid=np.isfinite(A[i,:4].sum())
  if pending and i>=due:
   if valid and A[i,4]>0:
    ep=A[i,0];qty=int(min(.5*cash/ep,min(2.,.02*cash)/(ep*(stop+2*(.0012+fr)))))
    if qty>0 and side*(target-ep)>0:pos=1;ei=i
   pending=0
  if pos and valid:
   op,hi,lo,close=A[i,:4];price=0.;reason=0
   if side*(op/ep-1)<=-stop:price=op;reason=1
   elif (lo<=ep*(1-stop) if side>0 else hi>=ep*(1+stop)):price=ep*(1-side*stop);reason=1
   elif A[i,4]>0 and (hi>target if side>0 else lo<target):price=target;reason=2
   elif i-ei>=168 or i==en-1:price=close;reason=3
   funding_count=(ts[i]+3600)//28800-ts[ei]//28800
   px=price if price else close;g=side*qty*(px-ep);fee=qty*(px+ep)*.0012;slip=qty*(px+ep)*fr;funding=-qty*px*fundrate*funding_count;net=g-fee-slip+funding
   if price:
    out[n]=np.array([ei,i,side,qty,ep,px,g,fee,slip,funding,net,reason]);n+=1;cash+=net;pos=0;cool=i+2
   eq=cash if not pos else cash+net
  else:eq=cash
  peak=max(peak,eq);dd=max(dd,peak-eq)
  if not pos and not pending and i>=cool and i<en-delay-2 and S[i,0]!=0:
   side=S[i,0];stop=S[i,1];target=S[i,2];pending=1;due=i+1+delay
 return out[:n],dd,cash
if __name__=='__main__':
 raw=json.load(gzip.open(P/'older_zec_hourly.json.gz','rt'));d=pd.DataFrame(raw['rows'],columns=['t','low','high','open','close','volume']).set_index('t').sort_index();ts=np.arange(d.index.min(),d.index.max()+3600,3600);d=d.reindex(ts)
 A=d[['open','high','low','close','volume']].to_numpy();A[:,:4]*=.01;lp=np.log(d.close);vol=lp.diff().rolling(72,min_periods=65).std(ddof=0).shift(1)*np.sqrt(24);ret=lp-lp.shift(12);side=np.sign(ret).where(ret.abs()>vol,0);stop=np.maximum(.005,2*vol);side=side.where(stop<=.12,0);S=np.c_[side.fillna(0),stop,np.array(d.close)*.01*(1+np.array(side)*3*stop)];S[~np.isfinite(S).all(axis=1),0]=0
 rows=[]
 for label in ['2022','2023','2024','2025','all']:
  st=0 if label=='all' else np.searchsorted(ts,int(pd.Timestamp(label+'-01-01',tz='UTC').timestamp()));en=len(ts) if label=='all' else np.searchsorted(ts,int(pd.Timestamp(str(int(label)+1)+'-01-01',tz='UTC').timestamp()))
  for scenario,fr,delay,fund in [('base',.0001,0,.0001),('combined',.0005,1,.0003),('no_funding',.0001,0,0)]:
   o,dd,cash=run(A,S,ts,st,en,fr,delay,fund);assert abs(cash-100-o[:,10].sum())<1e-7
   rows.append(dict(period=label,scenario=scenario,trades=len(o),net=float(o[:,10].sum()),dd=dd,without_best3=float(o[:,10].sum()-np.sort(o[:,10])[-3:].sum()),wins=int((o[:,10]>0).sum())))
   np.savez_compressed(P/f'older_zec_{label}_{scenario}.npz',ledger=o)
 pd.DataFrame(rows).to_csv(P/'older_results.csv',index=False);(P/'older_data_audit.json').write_text(json.dumps({'rows':len(d),'valid':int(d.close.notna().sum()),'source':raw['source'],'note':'Hourly underlying proxy; assumed spread/friction and adverse funding, not historical venue fills.'},indent=2));print(pd.DataFrame(rows).to_string(index=False))
