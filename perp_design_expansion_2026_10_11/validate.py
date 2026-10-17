from slow import *
from older_test import run as oldrun
if __name__=='__main__':
 t='KXZECPERP';ts,A,F,_=data_load(t);count=0
 for f in list(P.glob('zec_continuous_*.npz'))+list(P.glob('zec_[02]_*.npz')):
  z=np.load(f);o=z['ledger'];scenario=f.stem.split('_')[-1];fr,de,cc,mi,adv=SCEN[scenario];ei=o[:,2].astype(int);xi=o[:,3].astype(int);side=o[:,4];q=o[:,5];ep=o[:,6];xp=o[:,7];em=o[:,14];xm=o[:,15]
  assert (ei<xi).all() and (ei[1:]>xi[:-1]).all()
  assert np.allclose(o[:,8],side*q*(xp-ep));assert np.allclose(o[:,9],-side*q*(F[xi]-F[ei]))
  assert np.allclose(o[:,10],q*(ep*np.where(em,r.fees['maker_fee_rates'][t],r.fees['taker_fee_rates'][t])+xp*np.where(xm,r.fees['maker_fee_rates'][t],r.fees['taker_fee_rates'][t])))
  assert np.allclose(o[:,11],q*(ep+xp)*fr/1e4)
  assert np.allclose(o[:,12],o[:,8]+o[:,9]-o[:,10]-o[:,11])
  if 'continuous' in f.name:assert abs(z['path'][-1,1]-100-o[:,12].sum())<1e-7
  count+=len(o)
 older=0
 for f in P.glob('older_zec_*_*.npz'):
  o=np.load(f)['ledger'];assert np.allclose(o[:,6],o[:,2]*o[:,3]*(o[:,5]-o[:,4]));assert np.allclose(o[:,10],o[:,6]-o[:,7]-o[:,8]+o[:,9]);assert (o[:,1]>=o[:,0]).all();assert (o[1:,0]>o[:-1,1]).all();older+=len(o)
 rotation=0
 for f in (P/'rotation_ledgers').glob('*.npz'):
  z=np.load(f);o=z['ledger'];assert np.allclose(o[:,7],o[:,3]*o[:,4]*(o[:,6]-o[:,5]));assert np.allclose(o[:,11],o[:,7]+o[:,8]-o[:,9]-o[:,10]);assert abs(z['path'][-1]-100-o[:,11].sum())<1e-7;rotation+=len(o)
 # Synthetic hourly proxy: completed signal, following-open entry, both stop and target crossed => stop first.
 aa=np.tile(np.array([1.,1.,1.,1.,100.]),(10,1));ss=np.zeros((10,3));ss[1]=[1,.05,1.15];aa[2]=[1.,1.2,.9,1.1,100];tt=np.arange(10)*3600
 oo,_,_=oldrun(aa,ss,tt,0,10,.0001,0,0);assert len(oo)==1 and oo[0,0]==2 and oo[0,11]==1 and abs(oo[0,5]-.95)<1e-8
 results={'modern_zec_overlapping_rows':count,'older_proxy_overlapping_rows':older,'rotation_overlapping_rows':rotation,'hourly_stop_first_test':'passed','errors':0,'pair_audit':json.load(open(P/'pair_validation.json'))}
 (P/'validation.json').write_text(json.dumps(results,indent=2));print(results)
