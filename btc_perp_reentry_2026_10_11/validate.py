import json
from pathlib import Path
import numpy as np,pandas as pd
from study import run,fee,ts,fc,A,bounds,PERIODS
P=Path(__file__).resolve().parent
checks=0
for f in P.glob('ledger_*.parquet'):
 d=pd.read_parquet(f);assert (d.exit_i>d.entry_i).all();assert (d.entry_ts<d.exit_ts).all()
 if len(d)>1:assert (d.entry_i.to_numpy()[1:]>d.exit_i.to_numpy()[:-1]).all()
 np.testing.assert_allclose(d.gross,d.side*(d.exit_price-d.entry_price),atol=1e-12)
 expected=-d.side.to_numpy()*(fc[d.exit_i.astype(int)]-fc[d.entry_i.astype(int)])
 np.testing.assert_allclose(d.funding,expected,atol=1e-12)
 np.testing.assert_allclose(d.net,d.gross+d.funding-d.fees-d.slippage,atol=1e-12)
 checks+=len(d)
# Stop and target both crossed: stop first, delayed actual quote, never assumed trigger-price fill.
a=np.full((10,6),10.);signals=np.zeros((10,6));signals[0,0]=1
# Entry i1; stop and target i2; sale bid at i3 differs from either trigger.
a[2,2]=9.;a[2,4]=11.;a[3,:]=9.5
fund=np.zeros(10);fund[2:]=.01
out,_,_,_,amb=run(a,signals,fund,0,10,0,.05,.05,0,0.,1,.0012,False,False)
assert len(out)==1 and out[0,0]==1 and out[0,1]==3 and out[0,4]==9.5 and out[0,10]==1 and amb==1
assert abs(out[0,6]+.01)<1e-12
# Missing exit quote: wait until next real quote.
a[3,:]=np.nan;a[4,:]=9.4
out,_,_,_,_=run(a,signals,fund,0,10,0,.05,.05,0,0.,1,.0012,False,False)
assert out[0,1]==4 and abs(out[0,4]-9.4)<1e-12
# Short position uses ask to close, receives positive funding.
a=np.full((10,6),10.);a[:,1]=10.01;signals[0,0]=-1;a[2,3]=11.;a[3,1]=10.6
out,_,_,_,_=run(a,signals,fund,0,10,0,.05,.05,0,0.,1,.0012,False,False)
assert abs(out[0,4]-10.6)<1e-12 and abs(out[0,6]-.01)<1e-12
# Passive one-contract long reference, same baseline fees/friction and funding, no leverage.
passive=[]
for p in PERIODS:
 st,en=bounds(p);i=st
 while not np.isfinite(A[i,0]+A[i,1]):i+=1
 ep=A[i,1];xp=A[en-1,0];gross=xp-ep;fund=-(fc[en-1]-fc[i]);fees=(ep+xp)*.0012;slip=(ep+xp)*.0001
 passive.append(dict(period=p,gross=gross,funding=fund,fees=fees,slippage=slip,net=gross+fund-fees-slip))
pd.DataFrame(passive).to_csv(P/'passive_reference.csv',index=False)
receipt={'ledger_rows_checked':checks,'accounting_chronology_funding':'pass','synthetic_stop_first_nextquote_gap_short_funding':'pass','production_changes':False}
(P/'validation.json').write_text(json.dumps(receipt,indent=2));print(receipt)
