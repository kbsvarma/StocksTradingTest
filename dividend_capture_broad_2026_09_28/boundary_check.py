from portfolio import *
import inspect
# Sensitivity: include observed ex-dates whose planned exit lies after data cutoff.
# Value still-open positions at last close with no hypothetical sale fee.
source=inspect.getsource(simulate).replace('def simulate(', 'def simulate_with_open_tail(')
needle="candidates=GROUP.get((s,b,a),[]) if schedule is None else [e for bb in BEFORE for aa in AFTER for e in GROUP.get((s,bb,aa),[]) if schedule.get(DATES[int(e['i'])].year)==(bb,aa)]"
replacement=needle+"\n   if schedule is None:\n    candidates=list(candidates)+[dict(i=int(k-b),k=int(k),j=int(k+a)) for k in np.flatnonzero(DATA[s]['div']>0) if k+a>=N and k-b>=start]"
assert needle in source;source=source.replace(needle,replacement)
needle="l=pd.DataFrame(ledger,columns="
replacement="for s,p in pos.items():\n  if not buyhold:\n   value=p['q']*DATA[s]['close'][-1]\n   ledger.append((s,p['entry'],END,p['spent'],value,p['div'],value+p['div']-p['spent']))\n "+needle
source=source.replace(needle,replacement).replace('assert not pos','assert True')
source=source.replace("if keep:\n", "row['open_at_cutoff']=int(h[-1,4])\n if keep:\n")
exec(source,globals())
rows=[]
for s in [s for s in SYMS if CAT[s]!='benchmark']:
 r=simulate_with_open_tail([s],start_date='2025-01-02');rows.append(dict(ticker=s,category=CAT[s],**r))
pd.DataFrame(rows).to_csv(P/'open_tail_sensitivity.csv',index=False)
base=pd.read_csv(P/'security_portfolios.csv');base=base[(base.period=='common2025')&(~base.buyhold)&(base.before==5)&(base.after==5)]
x=pd.DataFrame(rows).merge(base[['ticker','ending_value']],on='ticker',suffixes=('_open','_complete'));x['difference']=x.ending_value_open-x.ending_value_complete
print('Open-tail max absolute terminal difference',x.difference.abs().max());print(x[x.open_at_cutoff>0][['ticker','open_at_cutoff','ending_value_open','ending_value_complete','difference']].round(2).to_string(index=False))
pools={c:ss for c,ss in U.items() if c!='benchmark'}
pools['core_income']=sum([U[c] for c in ['option_income','us_dividend_etf','reit','bdc','preferred_etf']],[])
pools['broad']=sum([ss for c,ss in U.items() if c!='benchmark'],[])
pr=[]
for cat,names in pools.items():
 names=[s for s in names if DATA[s]['start']<=DATES.searchsorted('2025-01-02')]
 pr.append(dict(pool=cat,period='common2025',names=len(names),**simulate_with_open_tail(names,start_date='2025-01-02',slots=4,liquidity=True)))
pd.DataFrame(pr).to_csv(P/'open_tail_rotation.csv',index=False)
qq=[]
for b in BEFORE:
 for a in AFTER:qq.append(dict(ticker='QQQI',period='all',**simulate_with_open_tail(['QQQI'],b,a)))
pd.DataFrame(qq).to_csv(P/'qqqi_open_tail_grid.csv',index=False)
