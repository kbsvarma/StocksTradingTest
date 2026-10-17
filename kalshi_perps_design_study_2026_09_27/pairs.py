from pathlib import Path
p=Path(__file__).resolve().parent;source=(p/'backtest.py').read_text();ns={'__file__':str(p/'backtest.py')};exec(source[:source.index('\nI={')]+source[source.index('\ndef leg_pnl'):source.index('\ndef directional')],ns);globals().update(ns)
results=[]
for aa,bb in [('BTC','ETH'),('BTC','SOL'),('ETH','SOL'),('BCH','LTC'),('GOLD','SILVER')]:
 a='KX'+aa+'PERP';b='KX'+bb+'PERP';times=sorted(set(C[a])|set(C[b]));rows=[];pos=None;marks=[];busy=0;skips=collections.Counter()
 def liquidate(T):
  xp=[leg_pnl(l,T) for l in pos['legs']];xp5=[leg_pnl(l,T,5) for l in pos['legs']]
  if any(x is None for x in xp):return None
  return dict(net=sum(x['net'] for x in xp),stress5=sum(x['net'] for x in xp5),fees=sum(x['fee'] for x in xp),funding=sum(x['funding'] for x in xp))
 for T in times:
  qa=Q[a].get(T);qb=Q[b].get(T)
  if pos:
   x=liquidate(T)
   if x is None:skips['missing_marks']+=1;continue
   marks.append(x['net']);z=(math.log(qa[2]/qb[2])-pos['mean'])/pos['sd'];age=(T-pos['entry_ts'])/3600
   if abs(z)<=.5 or abs(z)>=4 or age>=24:
    x=liquidate(T+3600)
    if x is None:skips['missing_exit']+=1;continue
    rows.append(dict(entry_ts=pos['entry_ts'],exit_ts=T+3600,legs=pos['legs'],marks=marks+[x['net']],**x));pos=None;marks=[];busy=T+3600
   continue
  if T<=busy or not qa or not qb or T+3600>END:continue
  if any((q[1]-q[0])/q[2]>.002 or float(C[t][T].get('volume') or 0)<=0 for t,q in [(a,qa),(b,qb)]):continue
  hist=[math.log(Q[a][s][2]/Q[b][s][2]) for s in range(T-72*3600,T,3600) if Q[a].get(s) and Q[b].get(s)]
  if len(hist)<65:continue
  mean=statistics.mean(hist);sd=statistics.pstdev(hist)
  if sd<=0:continue
  z=(math.log(qa[2]/qb[2])-mean)/sd
  if abs(z)<2:continue
  legs=[]
  for t,s in [(a,-1 if z>0 else 1),(b,1 if z>0 else -1)]:
   e=Q[t].get(T+3600)
   if not e:break
   ep=e[1] if s==1 else e[0];n=math.floor(25/ep)
   if n<1:break
   legs.append(dict(ticker=t,side=s,count=n,entry=ep,entry_ts=T+3600,signal_ts=T))
  if len(legs)!=2:skips['missing_entry_or_size']+=1;continue
  pos=dict(legs=legs,mean=mean,sd=sd,entry_ts=T+3600)
 if pos:
  common=[s for s in times if Q[a].get(s) and Q[b].get(s)];T=common[-1];x=liquidate(T)
  if T>pos['entry_ts']:rows.append(dict(entry_ts=pos['entry_ts'],exit_ts=T,legs=pos['legs'],marks=marks+[x['net']],**x))
 results.append(finish(a+'_'+b+'_ratio_reversion',rows,skips))
(P/'pair_results.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
