from slow import *
if __name__=='__main__':
 t='KXZECPERP';ts,A,F,_=data_load(t);S=slow_signals(t,ts,A,12,'momentum',2,3);out=[]
 for mode in [0,2]:
  for scenario in SCEN:
   fr,de,cc,mi,adv=SCEN[scenario];en=min(len(ts),np.searchsorted(ts,1791590400))
   while not np.isfinite(A[en-1,:2].sum()):en-=1
   o,path,dd,att,miss,amb,missing,held=r.replay(A,S,F,0,en,mode,5,fr/1e4,de,cc,mi,r.fees['taker_fee_rates'][t],r.fees['maker_fee_rates'][t],.0001,100,0,adv)
   assert abs(path[-1]-100-o[:,12].sum())<1e-7
   out.append(dict(mode=mode,scenario=scenario,trades=len(o),net=float(o[:,12].sum()),dd=dd,without_best3=float(o[:,12].sum()-np.sort(o[:,12])[-3:].sum()),gross=float(o[:,8].sum()),funding=float(o[:,9].sum()),fees=float(o[:,10].sum()),friction=float(o[:,11].sum()),missing_held=missing))
   np.savez_compressed(P/f'zec_continuous_{mode}_{scenario}.npz',ledger=o,path=np.c_[ts[:en],path])
   for period in ['early','later']:execute(t,ts,A,F,S,period,mode,scenario,save=P/f'zec_{mode}_{period}_{scenario}.npz')
 (P/'zec_continuous.json').write_text(json.dumps(out,indent=2));print(pd.DataFrame(out).to_string(index=False))
