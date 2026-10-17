from core import *
import time
E=pd.read_csv(P/'events.csv');GROUP={(s,int(b),int(a)):g.to_dict('records') for (s,b,a),g in E.groupby(['ticker','before','after'])}
BEFORE=(1,3,5,10);AFTER=(0,1,3,5,10)
(P/'ledgers').mkdir(exist_ok=True);(P/'daily').mkdir(exist_ok=True)

def simulate(names,b=5,a=5,start_date='2015-01-01',slots=1,cost=.0005,buyhold=False,extra_lag=0,high=False,declaration=False,keep=None,liquidity=False,schedule=None,allow_new=False,require_full=False):
 start=max(DATES.searchsorted(start_date),0 if allow_new else max(DATA[s]['start'] for s in names));years=(DAY[END]-DAY[start])/365.25
 entries={}
 if not buyhold:
  for s in names:
   candidates=GROUP.get((s,b,a),[]) if schedule is None else [e for bb in BEFORE for aa in AFTER for e in GROUP.get((s,bb,aa),[]) if schedule.get(DATES[int(e['i'])].year)==(bb,aa)]
   for e in candidates:
    i=int(e['i']);k=int(e['k'])
    if i<start:continue
    if high and (i-DATA[s]['start']<252 or DATA[s]['ttm'][i-1]<.03):continue
    if declaration and (k not in DATA[s]['decl'] or DATA[s]['decl'][k]>=i):continue
    if liquidity and (not np.isfinite(DATA[s]['adv'][i-1]) or DATA[s]['adv'][i-1]<1e6):continue
    entries.setdefault(i,[]).append((s,int(e['j'])))
  for i in entries:entries[i].sort(key=lambda x:(-float(DATA[x[0]]['priority'][i-1]),x[0]))
 cash=100000.;pending=[];pos={};hist=[];ledger=[];divsum=0.;costsum=0.;skipped=0
 for i in range(start,N):
  cash+=sum(amt for due,amt in pending if due<=i);pending=[x for x in pending if x[0]>i]
  for s,p in list(pos.items()):
   d=DATA[s];price=d['close'][i]
   if d['div'][i]>0:
    amount=p['q']*d['div'][i];divsum+=amount;p['div']+=amount
    due=int(d['due'][i])
    if extra_lag:due=int(np.searchsorted(DAY,DAY[min(due,END)]+extra_lag)) if due<=END else N+100
    pending.append((due,amount))
   if not buyhold and p['exit']==i:
    sale=p['q']*price*(1-cost);costsum+=p['q']*price*cost;cash_due=i+(1 if DATES[i]>=pd.Timestamp('2024-05-28') else 2);pending.append((cash_due,sale))
    ledger.append((s,p['entry'],i,p['spent'],sale,p['div'],sale+p['div']-p['spent']))
    del pos[s]
  equity=cash+sum(v for _,v in pending)+sum(p['q']*DATA[s]['close'][i] for s,p in pos.items())
  if buyhold:
   # Reinvest all available cash in equal fractions across held names, after dividends are payable.
   if cash>1e-8:
    spend=cash/len(names)
    for s in names:
     q=spend/(DATA[s]['close'][i]*(1+cost));costsum+=q*DATA[s]['close'][i]*cost
     if s not in pos:pos[s]=dict(q=0.,entry=i,spent=0.,div=0.,exit=N+1)
     pos[s]['q']+=q;pos[s]['spent']+=spend
    cash=0.
  else:
   for s,exit_i in entries.get(i,[]):
    if s in pos:continue
    if len(pos)>=slots or cash<equity*(.95/slots if require_full else .01):skipped+=1;continue
    spend=min(cash,equity/slots);q=spend/(DATA[s]['close'][i]*(1+cost));cash-=spend;costsum+=q*DATA[s]['close'][i]*cost
    pos[s]=dict(q=q,entry=i,spent=spend,div=0.,exit=exit_i)
  stock=sum(p['q']*DATA[s]['close'][i] for s,p in pos.items());receivable=sum(v for _,v in pending);equity=cash+stock+receivable
  assert cash>=-1e-6 and stock<=equity+1e-6 and (buyhold or len(pos)<=slots)
  hist.append((equity,stock,cash,receivable,len(pos)))
 h=np.array(hist);eq=h[:,0];r=eq/np.r_[100000,eq[:-1]]-1;dd=eq/np.maximum.accumulate(np.r_[100000,eq])[1:]-1
 l=pd.DataFrame(ledger,columns=['ticker','entry_i','exit_i','buy_cash','sale_cash','distributions','pnl'])
 if not buyhold:
  assert not pos
  error=100000+l.pnl.sum()-eq[-1];assert abs(error)<1e-5,error
 else:error=np.nan
 row=dict(require_full=require_full,start=str(DATES[start].date()),end=str(DATES[-1].date()),before=b,after=a,cost_bps=cost*10000,slots=slots,buyhold=buyhold,extra_lag=extra_lag,high_yield=high,declaration_only=declaration,ending_value=eq[-1],CAGR=(eq[-1]/100000)**(1/years)-1,total_return=eq[-1]/100000-1,max_drawdown=dd.min(),volatility=r.std(ddof=1)*np.sqrt(252),sharpe=r.mean()/r.std(ddof=1)*np.sqrt(252) if r.std()>0 else np.nan,exposure=np.mean(h[:,1]/eq),distributions=divsum,costs=costsum,trades=len(l),win_rate=(l.pnl>0).mean() if len(l) else np.nan,cashflow_error=error,skipped=skipped)
 if keep:
  dates=DATES[start:];dh=pd.DataFrame(h,index=dates,columns=['equity','invested','cash','receivable','positions']);dh['return_daily']=r;dh['drawdown']=dd
  # Exposure-matched reference, same basket, prior close exposure, zero cash yield, no rebalancing costs.
  basket=np.mean([DATA[s]['total'][start:] for s in names],axis=0);weights=np.r_[0,(h[:,1]/eq)[:-1]]
  dh['matched_reference']=100000*np.cumprod(1+weights*basket)
  dh.to_csv(P/'daily'/f'{keep}.csv',index_label='Date')
  if len(l):l['entry']=[str(DATES[x].date()) for x in l.entry_i];l['exit']=[str(DATES[x].date()) for x in l.exit_i]
  l.to_csv(P/'ledgers'/f'{keep}.csv',index=False)
  row['matched_ending']=dh.matched_reference.iloc[-1]
 return row

if __name__=='__main__':
 rows=[];count=0
 for s in SYMS:
  if CAT[s]=='benchmark':continue
  for b in BEFORE:
   for a in AFTER:
    save=f'{s}_all_b{b}_a{a}' if s=='QQQI' or (b,a)==(5,5) else None
    rows.append(dict(ticker=s,category=CAT[s],period='all',**simulate([s],b,a,keep=save)))
  rows.append(dict(ticker=s,category=CAT[s],period='all',**simulate([s],buyhold=True,keep=f'{s}_all_buyhold')))
  for period,dt in [('common2024','2024-02-01'),('common2025','2025-01-02')]:
   if DATA[s]['start']>DATES.searchsorted(dt):continue
   for b,a in [(1,1),(5,5)]:rows.append(dict(ticker=s,category=CAT[s],period=period,**simulate([s],b,a,start_date=dt,keep=f'{s}_{period}_b{b}_a{a}')))
   rows.append(dict(ticker=s,category=CAT[s],period=period,**simulate([s],start_date=dt,buyhold=True)))
  count+=1
  if count%10==0:print('Completed',count,'securities',flush=True)
 pd.DataFrame(rows).to_csv(P/'security_portfolios.csv',index=False)
 # Same-date category and broad rotations; new funds allowed only if available at common start.
 pools={c:ss for c,ss in U.items() if c!='benchmark'}
 pools['core_income']=sum([U[c] for c in ['option_income','us_dividend_etf','reit','bdc','preferred_etf']],[])
 pools['broad']=sum([ss for c,ss in U.items() if c!='benchmark'],[])
 rotations=[]
 for period,dt in [('common2024','2024-02-01'),('common2025','2025-01-02')]:
  for cat,allnames in pools.items():
   names=[s for s in allnames if DATA[s]['start']<=DATES.searchsorted(dt)]
   if not names:continue
   windows=[(b,a) for b in BEFORE for a in AFTER] if cat in ['core_income','broad','option_income'] else [(1,1),(5,5)]
   for b,a in windows:
    tag=f'rotation_{cat}_{period}_b{b}_a{a}'
    rotations.append(dict(pool=cat,period=period,names=len(names),**simulate(names,b,a,start_date=dt,slots=4,liquidity=True,keep=tag)))
   rotations.append(dict(pool=cat,period=period,names=len(names),**simulate(names,start_date=dt,buyhold=True,keep=f'rotation_{cat}_{period}_buyhold')))
 pd.DataFrame(rotations).to_csv(P/'rotation_portfolios.csv',index=False)
 sens=[]
 for s in ['QQQI','SPYI','JEPI','JEPQ','SCHD','O','MAIN','QDTE','TSLY']:
  for cost in [0,.0002,.001]:
   for b,a in [(1,1),(5,5)]:sens.append(dict(scope=s,**simulate([s],b,a,cost=cost)))
  sens.append(dict(scope=s,**simulate([s],extra_lag=30)))
  sens.append(dict(scope=s,**simulate([s],high=True)))
 for s in ['QQQI','SPYI','IWMI']:sens.append(dict(scope=s,**simulate([s],b=1,a=1,declaration=True)))
 for cat in ['core_income','broad','option_income']:
  names=[s for s in pools[cat] if DATA[s]['start']<=DATES.searchsorted('2025-01-02')]
  for cost in [0,.0002,.001]:sens.append(dict(scope=cat,**simulate(names,cost=cost,start_date='2025-01-02',slots=4,liquidity=True)))
  sens.append(dict(scope=cat,**simulate(names,extra_lag=30,start_date='2025-01-02',slots=4,liquidity=True)))
 pd.DataFrame(sens).to_csv(P/'sensitivity.csv',index=False)
 print('Completed',len(rows),'security paths and',len(rotations),'rotation/benchmark paths.',flush=True)
