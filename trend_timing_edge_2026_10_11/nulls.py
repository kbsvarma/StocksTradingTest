from rotation import *
rows=[]
for source in ['proxy','venue','transfer']:
 C,O,F=transfer_build(True) if source=='transfer' else old.build(source,True);src='proxy' if source=='transfer' else source;sig=extended(C)['weekly_top2'];end=periods(C,src)[0][2];valid=(np.log(C).shift(30).notna().shift(1).fillna(False)).to_numpy();rng=np.random.default_rng(121011);st=0
 observed,_,_=old.simulate(C,O,F,sig,src,True,st,end,list(range(4)),0.)
 shifts=np.arange(1,max(2,end//7))
 if len(shifts)>200:shifts=rng.choice(shifts,size=200,replace=False)
 for shift in shifts:
  S=np.roll(sig,int(shift)*7,axis=0);S[~valid]=0
  r,_,_=old.simulate(C,O,F,S,src,True,st,end,list(range(4)),0.);rows.append(dict(source=source,shift_weeks=int(shift),net=r['net'],observed=observed['net']))
 print(source,len(shifts),flush=True)
pd.DataFrame(rows).to_csv(P/'timing_nulls.csv',index=False)
