from engine import *

# Frozen extra checks: adverse intraminute bid/ask extremes (no favourable
# profit fills), fees rounded UP to cents each side, and block-bootstrap days.
# All are diagnostics, not a new parameter search.
res={};led={};paths={};quality={};data={}
for t,rule in [('KXSILVERPERP','h_reversal24'),('KXPALLADIUMPERP','m_reversal240')]:
 q,f=load(t,60);h,_=load(t,3600);hour=rule.startswith('h_');source=h if hour else q;ind=indicators(t,source,3600 if hour else 60,'hour' if hour else 'minute');bars={};bad=0
 for c in json.load(gzip.open(D/(t+'_1m.json.gz'),'rt'))['candlesticks']:
  s=c['end_period_ts']
  if s not in q:continue
  try:low=float(c['bid']['low']);high=float(c['ask']['high'])
  except (TypeError,ValueError):bad+=1;continue
  # Remove clearly erroneous placeholders. Invalid minutes are disclosed,
  # not silently interpreted as absence of a possible liquidation.
  if not(0<low<=q[s].bid<=q[s].ask<=high<1e8) or high/low>1.1:bad+=1;continue
  bars[s]=(low,high)
 quality[t]={'valid_adverse_bars':len(bars),'invalid_or_missing_among_valid_closes':bad}
 data[t]=(rule,q,f,source,ind,bars)
for label,a,b in [('early',START,split('KXSILVERPERP')),('later',split('KXSILVERPERP'),END),('full',START,END)]:
 for lev in [2,3]:
  for slip in [1,5]:
   pieces=[];rows=[]
   for t,(rule,q,f,source,ind,bars) in data.items():
    r,l,p=run(t,rule,q,f,ind,60,a,b,lev,slip,60,signal_Q=source,capital=50,daily_budget=3,reserve=10,dense_path=True,revalidate=True,adverse_bars=bars)
    pieces.append((r,{x['t']:x['equity'] for x in p}));rows+=l
   start=min(r['start'] for r,p in pieces);end=max(r['end'] for r,p in pieces);peak=100;dd=0;joint=[];missing=0
   for s in range(start,end+1,60):
    vs=[50 if s<r['start'] else 50+r['net'] if s>r['end'] else p.get(s) for r,p in pieces]
    if any(v is None for v in vs):missing+=1;continue
    eq=sum(vs);peak=max(eq,peak);dd=max(dd,peak-eq);joint.append({'t':s,'equity':eq})
   key=f'COMBINED100/{label}/L{lev}/slip{slip}/adverse_bars';extra=sum((math.ceil(r['qty']*r['price']*FEES[r['ticker']]*100-1e-12)+math.ceil(r['qty']*r['exit_price']*FEES[r['ticker']]*100-1e-12))/100-r['fees'] for r in rows)
   res[key]={'start':start,'end':end,'trades':len(rows),'wins':sum(r['net']>0 for r in rows),'net':sum(r['net'] for r in rows),'drawdown':dd,'without_best3':sum(r['net'] for r in rows)-sum(sorted((r['net'] for r in rows),reverse=True)[:3]),'cent_rounding_net_same_trades':sum(r['net'] for r in rows)-extra,'adverse_bar_exits':sum(r['reason'].startswith('intraminute') for r in rows),'missing_joint_marks':missing};led[key]=sorted(rows,key=lambda r:(r['entry'],r['ticker']));paths[key]=joint;print(key,res[key],flush=True)

bootstrap={};rng=np.random.default_rng(20261010)
for key in ['COMBINED100/full/L2/slip1/adverse_bars','COMBINED100/full/L3/slip1/adverse_bars']:
 path=paths[key];daily={}
 for r in path:daily[dt.datetime.fromtimestamp(r['t'],ET).date().isoformat()]=r['equity']
 values=np.diff([100]+list(daily.values()));n=len(values);blocks=np.array([[values[(i+j)%n] for j in range(3)] for i in range(n)]);samples=blocks[rng.integers(0,n,size=(10000,math.ceil(n/3)))].reshape(10000,-1)[:,:n].sum(axis=1)
 bootstrap[key]={'calendar_days':n,'pnl_interval_95':np.quantile(samples,[.025,.975]).tolist(),'description':'3-calendar-day circular blocks of marked daily PnL; exploratory uncertainty only, NOT corrected for strategy selection.'}
save('stress_results.json',res);save('stress_ledgers.json.gz',led);save('stress_paths.json.gz',paths);save('stress_diagnostics.json',{'bar_quality':quality,'bootstrap':bootstrap})
