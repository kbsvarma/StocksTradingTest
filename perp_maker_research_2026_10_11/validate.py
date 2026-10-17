from research import *
def synthetic():
 A=np.tile(np.array([10.,10.001,10.,10.001,10.,10.001,1000.]),(12,1));S=np.zeros((12,6));S[1]=[1,.02,10.02,100,1,0];F=np.zeros(12)
 A[3,:6]=[9.998,9.999,9.998,10.04,10.03,9.999]
 A[4,:6]=[10.02,10.021,10.02,10.021,10.02,10.021]
 A[5,:6]=[10.03,10.031,10.03,10.031,10.03,10.031]
 def run(a,cc=0,adv=0):return replay(a,S,F,0,12,2,1,.0001,1,cc,0,.0012,.0002,.001,100.,0.,adv)[0]
 o=run(A);assert len(o)==1 and o[0,2]==3 and o[0,3]==5 and o[0,15]==1 # no same-bar target, no touch fill
 B=A.copy();B[3,2]=9.7;o=run(B,adv=1);assert len(o)==1 and o[0,3]==4 and o[0,13]==1 and o[0,7]<=9.8 # stop-first and no rebound credit
 B=A.copy();B[3,1]=10.001;assert len(run(B,cc=1))==0 # low cross insufficient under close-cross
 B=A.copy();B[3,6]=0;assert len(run(B))==0 # no volume, no maker fill
 B=A.copy();B[2,1]=B[2,0];assert len(run(B))==0 # post-only rejects locked book
 B=A.copy();B[3,5]=10.;B[3,1]=10.;assert len(run(B))==0 # exact touch not through
 return 6
if __name__=='__main__':
 tests=synthetic();cache={};total=0;files=0;maxerr=0.;errors=[]
 for f in P.glob('KX*.parquet'):
  ticker=f.name.split('_')[0];scenario=f.stem.split('_')[-1]
  if f.stem.endswith('_adverse_stop'):scenario='adverse_stop'
  if ticker not in cache:cache[ticker]=load(ticker)[:3]
  ts,A,F=cache[ticker];l=pd.read_parquet(f);files+=1
  if len(l)==0:continue
  ei=l.entry_i.astype(int).to_numpy();xi=l.exit_i.astype(int).to_numpy();pi=l.post_i.astype(int).to_numpy();si=l.signal_i.astype(int).to_numpy();side=l.side.to_numpy();q=l.qty.to_numpy();ep=l.entry_price.to_numpy();xp=l.exit_price.to_numpy();em=l.entry_maker.to_numpy();xm=l.exit_maker.to_numpy();fr,de,cc,mi,mult=SCEN[scenario]
  assert (si<pi).all() and (pi<=ei).all() and (ei<xi).all(),f
  assert (ei[1:]>xi[:-1]).all(),f
  assert (q>=1).all() and (q==np.floor(q)).all(),f
  assert ((ei>pi)|(em==0)).all(),f
  gross=side*q*(xp-ep);funding=-side*q*(F[xi]-F[ei]);fees0=q*(ep*np.where(em,fees['maker_fee_rates'][ticker]*mult,fees['taker_fee_rates'][ticker])+xp*np.where(xm,fees['maker_fee_rates'][ticker]*mult,fees['taker_fee_rates'][ticker]));fric=q*(ep+xp)*fr/1e4
  for actual,expected in [(l.gross,gross),(l.funding,funding),(l.fees,fees0),(l.slippage,fric),(l.net,gross+funding-fees0-fric)]:
   err=float(np.max(np.abs(actual-expected)));maxerr=max(maxerr,err);assert err<1e-8,(f,err)
  for j in np.flatnonzero(em):
   i=ei[j];p=ep[j];cross=A[i,1 if side[j]>0 else 0] if cc else A[i,5 if side[j]>0 else 4]
   assert side[j]*(cross-p)<-1e-10 and A[i,6]>=q[j],(f,j,'entry fill')
  for j in np.flatnonzero(xm):
   i=xi[j];p=xp[j];cross=A[i,0 if side[j]>0 else 1] if cc else A[i,4 if side[j]>0 else 5]
   assert side[j]*(cross-p)>1e-10 and A[i,6]>=q[j],(f,j,'exit fill')
  total+=len(l)
 full_checks=0
 for f in P.glob('silver_flat_*.npz'):
  z=np.load(f);o=z['ledger'];path=z['path'];assert abs(path[-1,1]-100-o[:,12].sum())<1e-8
  assert np.allclose(o[:,8],o[:,4]*o[:,5]*(o[:,7]-o[:,6]))
  assert np.allclose(o[:,12],o[:,8]+o[:,9]-o[:,10]-o[:,11])
  assert (o[:,2]>o[:,1]).all() if 'taker_' not in f.name else (o[:,2]>=o[:,1]).all()
  assert (o[:,3]>o[:,2]).all();full_checks+=1
 result=dict(continuous_runs_checked=full_checks,synthetic_tests=tests,ledger_files=files,repeated_ledger_rows=total,max_accounting_error=maxerr,errors=errors,note='Overlapping scenario rows are not independent trades. Checks do not prove real fills.')
 (P/'validation.json').write_text(json.dumps(result,indent=2));print(result)
