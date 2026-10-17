from study import *
if __name__=='__main__':
 cache={};count=0;files=0
 for f in (P/'pair_ledgers').glob('*.npz'):
  a,b,h,trend,period,scenario=f.stem.split('_');o=np.load(f)['ledger'];files+=1
  if not len(o):continue
  for t in [a,b]:
   if t not in cache:cache[t]=data_load(t)[:3]
  ty,Y,Fy=cache[a];tx,X,Fx=cache[b];ts=np.arange(max(ty[0],tx[0]),min(ty[-1],tx[-1])+1,60);ei=o[:,0].astype(int);xi=o[:,1].astype(int);s=o[:,2];qy=o[:,3];qx=o[:,4];ey=o[:,5];ex=o[:,6];py=o[:,7];px=o[:,8]
  assert (xi>ei).all() and (ei[1:]>xi[:-1]).all(),f
  assert np.allclose(o[:,9],s*(qy*(py-ey)-qx*(px-ex))),f
  funding=-s*(qy*(Fy[np.searchsorted(ty,ts[xi])]-Fy[np.searchsorted(ty,ts[ei])])-qx*(Fx[np.searchsorted(tx,ts[xi])]-Fx[np.searchsorted(tx,ts[ei])]))
  assert np.allclose(o[:,10],funding),f
  fees=qy*(ey+py)*r.fees['taker_fee_rates'][a]+qx*(ex+px)*r.fees['taker_fee_rates'][b];assert np.allclose(o[:,11],fees),f
  assert np.allclose(o[:,13],o[:,9]+o[:,10]-o[:,11]-o[:,12]),f
  assert (qy*ey+qx*ex<=50+np.maximum(0,np.r_[0,np.cumsum(o[:-1,13])])*.5+1e-6).all(),f
  count+=len(o)
 (P/'pair_validation.json').write_text(json.dumps({'files':files,'overlapping_rows_checked':count,'errors':0},indent=2));print(files,count)
