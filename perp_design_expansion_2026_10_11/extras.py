from slow import *
if __name__=='__main__':
 results=[]
 for t in ['KXETHPERP','KXXRPPERP','KXBNBPERP','KXHYPEPERP','KXZECPERP']:
  ts,A,F,_=data_load(t)
  for family in ['momentum','breakout','overshoot','trendpullback']:
   for h in [30,120,360]:
    S=signals(t,ts,A,h,family)
    for mode in [0,2]:
     for period in ['early','later']:
      for scenario in SCEN:
       v=execute(t,ts,A,F,S,period,mode,scenario)
       if v:v.update(family=family,horizon=h,stop_mult=0,target_R=2,speed='intraday');results.append(v)
  for family in ['breakout','momentum','pullback']:
   for h in [12,24,72]:
    for mult in [1,2]:
     for target in [3,0]:
      S=slow_signals(t,ts,A,h,family,mult,target)
      for mode in [0,2]:
       for period in ['early','later']:
        for scenario in SCEN:
         v=execute(t,ts,A,F,S,period,mode,scenario)
         if v:v.update(family=family,horizon=h,stop_mult=mult,target_R=target,speed='slow');results.append(v)
  print(t,'done',flush=True);pd.DataFrame(results).to_csv(P/'extra_results.csv',index=False)
