"""Independent raw-file cashflow reconciliation and output invariants."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
P=Path(__file__).resolve().parent;ROOT=P.parent;D=P/'results'
t=pd.read_parquet(D/'trades.parquet');p=pd.read_parquet(D/'predictions.parquet');f=pd.read_parquet(D/'fits.parquet')
assert not p.duplicated(['day','time','fold','model','cp','K']).any()
assert ((np.rint(p.K/5).astype(int)%2)==p.fold).all()
assert np.allclose(p.error,p.pred-p.mid)
assert (p.bid>0).all() and (p.ask>=p.bid).all()
executed=t[t.status!='missing_entry']
assert np.allclose(executed.pnl,executed.direction*(executed.exit-executed.entry)*100-2.60)
assert np.allclose(executed.pnl_stress,executed.pnl-20)
assert not t.duplicated(['day','time','model','horizon']).any()
manifest=json.loads((D/'manifest.json').read_text())
checks=[]
# Stratified deterministic sample, then largest observed gain and loss.
sample=t[(t.status=='ok')&(t.horizon==15)].copy();sample['year']=sample.day.str[:4]
sample=pd.concat([sample.groupby(['year','model','direction']).head(1),sample.nlargest(1,'pnl'),sample.nsmallest(1,'pnl')]).drop_duplicates(['day','time','model','horizon'])
for _,r in sample.iterrows():
    sources=[ROOT/x['path'] for x in manifest['sources'] if r.day in x['path']]
    frames=[]
    for path in sources:
        q=pd.read_parquet(path,columns=['symbol','bid_px_00','ask_px_00']);q['stamp']=q.index
        frames.append(q.reset_index(drop=True))
    q=pd.concat(frames,ignore_index=True).drop_duplicates(['stamp','symbol'],keep='last')
    q=q.set_index(['stamp','symbol'])
    entry=pd.Timestamp(r.day+' '+r.time,tz='America/New_York')+pd.Timedelta(minutes=1);exit=entry+pd.Timedelta(minutes=int(r.horizon))
    symbols=[f'SPXW  {r.day[2:]}{r.cp}{int(k*1000):08d}' for k in [r.buyK,r.sellK]]
    eb=[q.loc[(entry,s),'bid_px_00'] for s in symbols];ea=[q.loc[(entry,s),'ask_px_00'] for s in symbols]
    xb=[q.loc[(exit,s),'bid_px_00'] for s in symbols];xa=[q.loc[(exit,s),'ask_px_00'] for s in symbols]
    if r.direction==1:cash=(-ea[0]+eb[1]+xb[0]-xa[1])*100-2.60
    else:cash=(eb[0]-ea[1]-xa[0]+xb[1])*100-2.60
    assert abs(cash-r.pnl)<1e-7,(r.day,cash,r.pnl)
    checks.append(dict(day=r.day,time=r.time,model=r.model,direction=int(r.direction),symbols=symbols,raw_cashflow=cash,stored_pnl=r.pnl))
result=dict(status='PASS',independent_raw_cashflows=len(checks),checks=checks,prediction_rows=len(p),trade_horizon_rows=len(t),invariants=['unique predictions','heldout fold identity','price error arithmetic','valid bid/ask','cashflow arithmetic','stress arithmetic','one signal per model/snapshot/horizon'])
(D/'verification.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='checks'},indent=2))
