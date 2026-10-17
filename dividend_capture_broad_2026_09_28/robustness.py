from portfolio import *
rows=[]
for s in ['QQQI','SPYI','JEPI','JEPQ','DIVO','TSLY','NVDY','CONY','QDTE','EPR','STAG']:
 for period,dt in [('all','2015-01-01'),('common2025','2025-01-02')]:
  rows.append(dict(scope=s,period=period,setting='95% funding required',**simulate([s],start_date=dt,require_full=True)))
 # Opening-price execution: same scheduled entry day; exits remain at close.
 # Temporarily replace quote for fills only via a wrapper derived from the engine source.
import inspect
source=inspect.getsource(simulate).replace('def simulate(', 'def simulate_entry_open(')
source=source.replace("q=spend/(DATA[s]['close'][i]*(1+cost));cash-=spend;costsum+=q*DATA[s]['close'][i]*cost", "q=spend/(float(DATA[s]['raw'].loc[DATES[i],'Open'])*(1+cost));cash-=spend;costsum+=q*float(DATA[s]['raw'].loc[DATES[i],'Open'])*cost")
exec(source,globals())
for s in ['QQQI','SPYI','JEPI','JEPQ','DIVO','TSLY','NVDY','QDTE','EPR','STAG']:
 rows.append(dict(scope=s,period='all',setting='Enter at open of scheduled day',**simulate_entry_open([s])))
for cat in ['option_income','core_income','broad']:
 if cat=='core_income':names=sum([U[c] for c in ['option_income','us_dividend_etf','reit','bdc','preferred_etf']],[])
 elif cat=='broad':names=sum([ss for c,ss in U.items() if c!='benchmark'],[])
 else:names=U[cat]
 names=[s for s in names if DATA[s]['start']<=DATES.searchsorted('2025-01-02')]
 rows.append(dict(scope=cat,period='common2025',setting='95% target funding required',**simulate(names,start_date='2025-01-02',slots=4,liquidity=True,require_full=True)))
 rows.append(dict(scope=cat,period='common2025',setting='Exclude synthetic spin-off adjustments',**simulate([s for s in names if s not in ['O','T','WPC','MMM']],start_date='2025-01-02',slots=4,liquidity=True)))
pd.DataFrame(rows).to_csv(P/'robustness.csv',index=False)
# Compare winners' training and recent periods using the SAME predeclared -5/+5 rule.
regimes=[]
for s in ['QQQI','SPYI','JEPI','JEPQ','DIVO','TSLY','QDTE','STAG','EPR','NVDY','CONY']:
 f=P/'daily'/f'{s}_all_b5_a5.csv';d=pd.read_csv(f,index_col=0,parse_dates=True)
 for first,last in [('2015','2019'),('2020','2022'),('2023','2024'),('2025','2026')]:
  g=d.loc[first:last]
  if len(g)<20:continue
  factor=(1+g.return_daily).prod()
  regimes.append(dict(ticker=s,period=first+'-'+last,sessions=len(g),total_return=factor-1,annualized_return=factor**(252/len(g))-1))
pd.DataFrame(regimes).to_csv(P/'regime_returns.csv',index=False)
print(pd.DataFrame(rows).query("scope in ['QQQI','TSLY','EPR','STAG']")[['scope','period','setting','ending_value','CAGR','max_drawdown','trades']].round(5).to_string(index=False))
