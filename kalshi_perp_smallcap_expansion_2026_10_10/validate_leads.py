from engine import *
leads=[('KXDOGEPERP','h_channel48'),('KXDOGEPERP','h_ema8_32'),('KXLTCPERP','h_channel48'),('KXSILVERPERP','h_reversal24')]
res={};led={};paths={}
for t,rule in leads:
 h,_=load(t,3600);q,f=load(t,60);i=indicators(t,h,3600,'hour')
 for name,a,b in [('early',START,split(t)),('later',split(t),END),('full',START,END)]:
  for lev,slip,delay in [(1,1,60),(2,1,60),(3,1,60),(2,5,60),(3,5,60),(2,1,120)]:
   k=f'{t}/{rule}/{name}/L{lev}/slip{slip}/delay{delay}';r,l,p=run(t,rule,q,f,i,60,a,b,lev,slip,delay,signal_Q=h);res[k]=r;led[k]=l;paths[k]=p
   print(k,r['trades'],round(r['net'],4),round(r['drawdown'],4),flush=True)
save('lead_results.json',res);save('lead_ledgers.json.gz',led);save('lead_paths.json.gz',paths)
