from pathlib import Path
import json, hashlib, importlib.util
import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar, brentq
P=Path(__file__).resolve().parent; R=P.parent
spec=importlib.util.spec_from_file_location('engine',R/'cover_rebalance_2026_09/backtest.py'); e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
sel=json.loads((P/'selection.json').read_text()); assert hashlib.sha256((P/'PROTOCOL.md').read_bytes()).hexdigest()==sel['protocol_sha256']
start=pd.Timestamp(sel['cutoff'])
def load(n):
 p=R/f'stock_overnight_2026_09/data/{n}.parquet';d=pd.read_parquet(p);d.index=pd.to_datetime(d.index.date);assert not d.index.duplicated().any();return d
calendar=load('SPY').loc[start:].index
raw={}
for n,path in sel['source_paths'].items():
 assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==sel['source_sha256'][n]
 raw[n]=load(n).reindex(calendar)
summary=[];daily=[];details=[];annual=[];audits=[];checks=[];outcomes=[];rng=np.random.default_rng(20260913)
for pair in sel['pairs']:
 name=pair['pair']; names=[pair['a'],pair['b']]
 prices=pd.DataFrame({n:raw[n]['Adj Close'] for n in names});valid=(np.isfinite(prices)&(prices>0)).all(axis=1)
 assert valid.iloc[0]; complete=bool(valid.all()); prices.loc[~valid].to_csv(P/f'{name}_missing.csv')
 if not complete:prices=prices.iloc[:np.flatnonzero(~valid)[0]]
 prices.to_csv(P/f'{name}_prices.csv');G=(prices/prices.shift()).iloc[1:].to_numpy();dates=prices.index[1:];years=(dates[-1]-start).days/365.25
 U,wealth=e.universal(G,1001);_,fine=e.universal(G,10001)
 obj=lambda b:-np.log(b*G[:,0]+(1-b)*G[:,1]).sum()
 opt=minimize_scalar(obj,bounds=(0,1),method='bounded');best=min([0.,1.,float(opt.x)],key=obj)
 specs={'daily_50_50':(.5,np.full(len(G),.5)),'hold_50_50':(.5,None),'universal':(.5,U),'hindsight_oracle':(best,np.full(len(G),best)),names[0]+'_hold':(1.,None),names[1]+'_hold':(0.,None)}
 curves={};stats={}
 for model,(w,targets) in specs.items():
  for bps in [0,5,10]:
   c=bps/10000;d=e.simulate(G,dates,w,c,targets);d['pair']=name;d['strategy']=model;d['cost_bps']=bps;daily.append(d);curves[model,bps]=d
   met=e.metrics(d,100000,years);met['cost_dollars']+=100000*c;met['annual_turnover']+=1/years
   row=dict(pair=name,strategy=model,cost_bps=bps,**met);summary.append(row);stats[model,bps]=row
   previous=100000
   for yr,g in d.groupby(d.date.dt.year):
    last=g.wealth.iloc[-1];annual.append(dict(pair=name,strategy=model,cost_bps=bps,year=int(yr),return_net=float(last/previous-1)));previous=last
   if model in ['daily_50_50','hold_50_50']:
    # Independent share ledger with dollar cashflow root solve on adjusted price units.
    shares=100000*(1-c)*np.array([w,1-w])/prices.iloc[0].to_numpy();ledger=[]
    for i,px in enumerate(prices.iloc[1:].to_numpy()):
     values=shares*px;v=values.sum()
     if i==len(G)-1:shares*=1-c
     elif targets is not None:
      t=np.array([targets[i],1-targets[i]])
      after=brentq(lambda z:z+c*np.abs(z*t-values).sum()-v,0,v,xtol=1e-8)
      shares=after*t/px
     ledger.append(float((shares*px).sum()))
    err=float(np.max(np.abs(np.array(ledger)/d.wealth.to_numpy()-1)));assert err<1e-10
    checks.append(dict(pair=name,strategy=model,cost_bps=bps,share_ledger_max_relative_error=err))
 assert np.allclose(curves['universal',0].wealth/100000,wealth,rtol=1e-10)
 assert np.isclose(curves['hold_50_50',0].wealth.iloc[-1]/100000,.5*np.prod(G[:,0])+.5*np.prod(G[:,1]))
 logs=np.log(G);bonus=np.log(G.mean(axis=1))-logs.mean(axis=1);assert bonus.min()>-1e-12
 assert abs(np.log(curves['daily_50_50',0].wealth.iloc[-1]/100000)-logs.mean(axis=1).sum()-bonus.sum())<1e-10
 crp=curves['daily_50_50',5];hold=curves['hold_50_50',5]
 relative=np.log1p(crp.return_net.to_numpy())-np.log1p(hold.return_net.to_numpy())
 halves=[float(relative[dates.year<=2023].sum()),float(relative[dates.year>=2024].sum())]
 n=len(relative);starts=rng.integers(0,n-20+1,size=(2000,int(np.ceil(n/20))));idx=(starts[:,:,None]+np.arange(20)).reshape(2000,-1)[:,:n]
 ci=np.quantile(relative[idx].mean(axis=1)*252,[.025,.975])
 s=stats['daily_50_50',5];h=stats['hold_50_50',5]
 conditions={'complete_at_least_3_years':complete and years>=3,'positive_cagr_and_2pp_advantage':s['cagr']>0 and s['cagr']-h['cagr']>=.02,'advantage_in_both_halves':min(halves)>0,'advantage_at_10bps':stats['daily_50_50',10]['cagr']>stats['hold_50_50',10]['cagr'],'drawdown_limit':s['max_drawdown']>=-.60 and s['max_drawdown']>=h['max_drawdown']-.05}
 outcomes.append(dict(pair=name,promising=all(conditions.values()),cagr=s['cagr'],hold_cagr=h['cagr'],advantage_pp=100*(s['cagr']-h['cagr']),wealth=s['terminal_wealth'],hold_wealth=h['terminal_wealth'],max_drawdown=s['max_drawdown'],failed_conditions='; '.join(k for k,v in conditions.items() if not v),relative_log_2022_2023=halves[0],relative_log_2024_on=halves[1],bootstrap_annual_log_lower=float(ci[0]),bootstrap_annual_log_upper=float(ci[1])))
 details.append(dict(pair=name,start=str(start.date()),end=str(dates[-1].date()),years=years,days=n,complete=complete,training_rho=pair['correlation'],test_rho=float(np.corrcoef(logs.T)[0,1]),test_vol=dict(zip(names,(logs.std(axis=0,ddof=1)*np.sqrt(252)).tolist())),hindsight_weight_a=best,weighted_stock_log_growth= float(logs.mean(axis=1).sum()/years),diversification_log_growth=float(bonus.sum()/years),universal_grid_error=float(wealth[-1]/fine[-1]-1),conditions=conditions))
 for ticker in names:
  d=raw[ticker].loc[prices.index];g=d['Adj Close']/d['Adj Close'].shift();alt=(d.Close+d.Dividends)/d.Close.shift()
  for day in d.index[(abs(g-alt)>.005)|(abs(g-1)>.20)]:
   audits.append(dict(pair=name,symbol=ticker,date=str(day.date()),adjusted_return=float(g.loc[day]-1),adjustment_discrepancy=float(g.loc[day]-alt.loc[day]),dividend=float(d.loc[day,'Dividends']),split=float(d.loc[day,'Stock Splits'])))
 print(name,'promising',all(conditions.values()),'CAGR',s['cagr'],'hold',h['cagr'],flush=True)
pd.DataFrame(summary).to_csv(P/'summary.csv',index=False);D=pd.concat(daily,ignore_index=True);D.to_parquet(P/'daily.parquet',index=False)
pd.DataFrame(annual).to_csv(P/'annual.csv',index=False);pd.DataFrame(outcomes).to_csv(P/'outcomes.csv',index=False);pd.DataFrame(checks).to_csv(P/'verification.csv',index=False)
pd.DataFrame(audits,columns=['pair','symbol','date','adjusted_return','adjustment_discrepancy','dividend','split']).to_csv(P/'price_audit.csv',index=False)
(P/'details.json').write_text(json.dumps(details,indent=2))
proxy_names=['META','GOOGL','MSFT','NVDA','AMD','AVGO','AMZN','MU','DELL','INTC','TSM']
proxy_prices=pd.DataFrame({n:load(n)['Adj Close'] for n in proxy_names+['SPY']}).reindex(calendar)
assert proxy_prices.notna().all().all()
proxy=proxy_prices.pct_change().iloc[1:]; correlations=D[(D.strategy=='daily_50_50')&(D.cost_bps==5)].pivot(index='date',columns='pair',values='return_net')
correlations['SPY']=proxy.SPY;correlations['equal_weight_holdings_proxy']=proxy[proxy_names].mean(axis=1);correlations.corr().to_csv(P/'correlations.csv')
(P/'run_metadata.json').write_text(json.dumps(dict(engine_path=str(R/'cover_rebalance_2026_09/backtest.py'),engine_sha256=hashlib.sha256((R/'cover_rebalance_2026_09/backtest.py').read_bytes()).hexdigest(),selection_sha256=hashlib.sha256((P/'selection.json').read_bytes()).hexdigest(),proxy_names=proxy_names,bootstrap_seed=20260913,bootstrap_draws=2000,block_sessions=20),indent=2))
