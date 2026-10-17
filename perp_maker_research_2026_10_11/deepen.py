from research import *
SCEN["combined"]=(5,2,1,1,2.5)
def flat_signals(t,ts,A):
 H=old.load(t,3600)[0];I=old.indicators(t,H,3600,'hour');raw=signals(t,'m_reversal240',ts,A,None);S=raw.copy()
 for i,s in enumerate(ts):
  j=I.get((int(s)-1)//3600*3600)
  if not j or abs(j['trend'])>=j['vol']:S[i,0]=0
 return raw,S

def fullrun(t,ts,A,F,S,mode,scen):
 st=0;en=len(ts)-1
 while not np.isfinite(A[en-1,0]+A[en-1,1]):en-=1
 fr,de,cc,mi,mult=SCEN[scen];name,mk,ttl=mode;tick=max(float(old.M[t]['tick_size']),.0005 if t=='KXSILVERPERP' else 0)
 o,path,dd,att,miss,amb,missing,held=replay(A,S,F,st,en,mk,ttl,fr/1e4,de,cc,mi,fees['taker_fee_rates'][t],fees['maker_fee_rates'][t]*mult,tick,100,0,int(scen in ['adverse_stop','combined']))
 return dict(ticker=t,mode=name,scenario=scen,trades=len(o),net=float(o[:,12].sum()),gross=float(o[:,8].sum()),funding=float(o[:,9].sum()),fees=float(o[:,10].sum()),friction=float(o[:,11].sum()),dd=dd,wins=int((o[:,12]>0).sum()),without_best3=float(o[:,12].sum()-np.sort(o[:,12])[-3:].sum()),attempts=att,missed=miss,missing_held=missing,held=held),o,np.c_[ts[st:en],path]
if __name__=='__main__':
 t='KXSILVERPERP';ts,A,F,_=load(t);raw,S=flat_signals(t,ts,A);results=[];periods=[]
 for mode in [MODES[0],MODES[2],MODES[4]]:
  for scen in SCEN:
   r,o,p=fullrun(t,ts,A,F,S,mode,scen);results.append(r);
   for period in ['early','later']:
    rr,_,_=execute(t,'m_reversal240',ts,A,F,S,period,mode,scen);periods.append(rr)
   np.savez_compressed(P/f'silver_flat_{mode[0]}_{scen}.npz',ledger=o,path=p)
 (P/'silver_period_results.json').write_text(json.dumps(periods,indent=2))
 parent=[]
 for mode in [MODES[0],MODES[2],MODES[4]]:
  for scen in ['base','combined']:
   rr,_,_=fullrun(t,ts,A,F,raw,mode,scen);parent.append(rr)
 (P/'silver_parent_results.json').write_text(json.dumps(parent,indent=2))
 (P/'silver_full_results.json').write_text(json.dumps(results,indent=2))
 # Calibrate random thinning on EARLY TRADE COUNT ONLY; P&L never selects p.
 target=pd.read_csv(P/'hybrid_results.csv').query("ticker=='KXSILVERPERP' and rule=='m_reversal240_flat' and mode=='entry5' and scenario=='base' and period=='early'").iloc[0].trades
 calibr=[];hours=(ts//3600-ts[0]//3600).astype(int)
 for prob in [.02,.05,.1,.2,.3,.4,.6,.8]:
  counts=[]
  for seed in range(20):
   rng=np.random.default_rng(seed);mask=rng.random(hours.max()+1)<prob;R=raw.copy();R[~mask[hours],0]=0
   r,_,_=execute(t,'m_reversal240',ts,A,F,R,'early',MODES[2],'base');counts.append(r['trades'])
  calibr.append(dict(prob=prob,median_trades=float(np.median(counts)),difference=abs(float(np.median(counts))-target)))
 prob=min(calibr,key=lambda x:x['difference'])['prob'];null=[]
 for seed in range(1000,1500):
  rng=np.random.default_rng(seed);mask=rng.random(hours.max()+1)<prob;R=raw.copy();R[~mask[hours],0]=0
  r,_,_=fullrun(t,ts,A,F,R,MODES[2],'base');r['seed']=seed;null.append(r)
 d=pd.DataFrame(null);d.to_csv(P/'silver_random_null.csv',index=False);act=next(r for r in results if r['mode']=='entry5' and r['scenario']=='base');matched=d[(d.trades-act['trades']).abs()<=2]
 summary={'calibration':calibr,'selected_probability':prob,'null_runs':len(d),'actual_trades':act['trades'],'actual_net':act['net'],'matched_count':len(matched),'matched_trade_range':[act['trades']-2,act['trades']+2],'matched_median_net':float(matched.net.median()),'matched_p95_net':float(matched.net.quantile(.95)),'matched_fraction_at_least_actual':float((matched.net>=act['net']).mean()),'all_null_median_trades':float(d.trades.median()),'all_null_median_net':float(d.net.median())}
 # Descriptive 3-calendar-day block bootstrap of marked daily P&L, not selection corrected.
 z=np.load(P/'silver_flat_entry5_base.npz');path=z['path'];day=(path[:,0]//86400).astype(int);end=pd.DataFrame({'day':day,'equity':path[:,1]}).groupby('day').equity.last();daily=np.diff(np.r_[100,end.to_numpy()]);rng=np.random.default_rng(42);sums=[]
 for _ in range(10000):
  starts=rng.integers(0,len(daily),int(np.ceil(len(daily)/3)));ix=np.concatenate([(s+np.arange(3))%len(daily) for s in starts])[:len(daily)];sums.append(float(daily[ix].sum()))
 summary['daily_block_bootstrap_95']=list(np.quantile(sums,[.025,.975]));summary['net_per_trade']=act['net']/act['trades'];summary['sample_days']=(path[-1,0]-path[0,0])/86400
 (P/'silver_diagnostics.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
