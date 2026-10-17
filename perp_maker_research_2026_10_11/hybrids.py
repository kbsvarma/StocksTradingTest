from research import *
if __name__=='__main__':
 results=[]
 for t in old.M:
  if old.M[t]['asset_class'] not in ['Metals','Indices'] or not(D/(t+'_1m.json.gz')).exists():continue
  if not json.load(gzip.open(D/(t+'_1m.json.gz'),'rt'))['candlesticks']:continue
  ts,A,F,_=load(t);H=old.load(t,3600)[0];I=old.indicators(t,H,3600,'hour');align=np.zeros(len(ts));flat=np.zeros(len(ts),bool)
  for i,s in enumerate(ts):
   prev=(int(s)-1)//3600*3600;j=I.get(prev)
   if j:align[i]=np.sign(j['trend']);flat[i]=abs(j['trend'])<j['vol']
  for rule in ['m_reversal60','m_reversal240']:
   raw=signals(t,rule,ts,A,None);mid=(A[:,0]+A[:,1])/2;reward=raw[:,0]*(raw[:,2]/mid-1);cost=(A[:,1]-A[:,0])/mid+2*fees['taker_fee_rates'][t]+.0002;good=(reward>=3*cost)&(reward>=.5*raw[:,1]);same=raw[:,0]*align>0
   for filtername,gate in [('aligned',same),('flat',flat),('opportunity',good),('aligned_opportunity',same&good)]:
    S=raw.copy();S[~gate,0]=0
    for mode in [MODES[0],MODES[2],MODES[4]]:
     for period in ['early','later']:
      for scenario in SCEN:
       r,l,path=execute(t,rule,ts,A,F,S,period,mode,scenario)
       if r:
        r['rule']=rule+'_'+filtername;results.append(r);key='_'.join([t,r['rule'],mode[0],period,scenario]);l.to_parquet(P/(key+'.parquet'),index=False)
   print(t,rule,'hybrids done',flush=True)
  pd.DataFrame(results).to_csv(P/'hybrid_results.csv',index=False)
