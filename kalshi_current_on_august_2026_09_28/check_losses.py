import contextlib,io,json,math,statistics
with contextlib.redirect_stdout(io.StringIO()):import replay as r
old={'KXETH15M-26AUG081045-45':1786199728,'KXETH15M-26AUG101730-30':1786397182,'KXETH15M-26AUG110300-00':1786431321,'KXXRP15M-26AUG110630-30':1786443801}
out=[]
for t,oldts in old.items():
 m=r.markets[t]; start=int(r.ts(m['open_time']));close=int(r.ts(m['close_time']));bars=r.spots[m['asset']];vals=[]
 for T in range(start+300,close-120,60):
  c=r.candles[t][T]
  try:b,a=float(c['bc']),float(c['ac'])
  except (TypeError,ValueError):continue
  side=1 if b+a>1 else -1; bid,ask=(b,a) if side==1 else (1-a,1-b)
  keys=range(T-3660,T,60)
  if not all(k in bars for k in keys):continue
  vv=[bars[k] for k in keys];sd=statistics.pstdev(math.log(y/x) for x,y in zip(vv,vv[1:]));z=side*(bars[T-60]/bars[start-60]-1)/(sd*math.sqrt((close-T)/60))
  if .85-1e-8<=ask<.93-1e-8 and ask-bid<=.02000001:vals.append(dict(T=T,z=z,ask=ask,side=side))
 out.append(dict(ticker=t,old_entry_ts=oldts,current_price_qualified_minutes=vals,current_signals=[s for s in r.signals if s['ticker']==t]))
(r.P/'old_loss_comparison.json').write_text(json.dumps(out,indent=2))
for x in out:print(x['ticker'],'price-qualified z values:',[round(v['z'],3) for v in x['current_price_qualified_minutes']],'current signals',len(x['current_signals']))
print('book delays:',[int(x['entry_ts']-x['decision_ts']) for x in r.result['scenarios'][0]['rows']])
