"""One frozen pair. Full daily constant-weight and Cover-mixture accounting."""
from pathlib import Path
import json,hashlib
import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
P=Path(__file__).resolve().parent;R=P.parent
COSTS=[0,1,5,10];INITIAL=100000.

def self_financing(preweights,target,c):
    x=1.
    for _ in range(12):x=1-c*np.abs(x*target-preweights).sum()
    assert abs(x+c*np.abs(x*target-preweights).sum()-1)<1e-12
    return x,float(np.abs(x*target-preweights).sum())

def simulate(gross,dates,w0,c,targets=None):
    hold=(1-c)*np.array([w0,1-w0]);rows=[]
    for i,(date,rr) in enumerate(zip(dates,gross)):
        prev=float(hold.sum());hold*=rr;before=float(hold.sum());traded=0.;cost=0.
        if i==len(gross)-1:
            traded=before;cost=c*traded;hold*=1-c
        elif targets is not None:
            target=np.array([targets[i],1-targets[i]]);x,turn=self_financing(hold/before,target,c)
            traded=before*turn;cost=before*(1-x);hold=before*x*target
        after=float(hold.sum())
        rows.append(dict(date=date,wealth=after*INITIAL,return_net=after/prev-1,weight_a=hold[0]/after,traded_dollars=traded*INITIAL,cost_dollars=cost*INITIAL,turnover=traded/before))
    return pd.DataFrame(rows)

def universal(gross,n):
    b=np.linspace(0,1,n);q=np.ones(n)/(n-1);q[[0,-1]]*=.5;logs=np.zeros(n);targets=[];wealth=[]
    for r in gross:
        logs+=np.log(b*r[0]+(1-b)*r[1]);m=logs.max();ww=q*np.exp(logs-m)
        targets.append(float(np.dot(ww,b)/ww.sum()));wealth.append(float(np.exp(m)*ww.sum()))
    return np.array(targets),np.array(wealth)

def metrics(d,baseline,years):
    w=np.r_[baseline,d.wealth.to_numpy()];dd=w/np.maximum.accumulate(w)-1
    return dict(terminal_wealth=float(w[-1]),multiple=float(w[-1]/INITIAL),cagr=float((w[-1]/INITIAL)**(1/years)-1),max_drawdown=float(dd.min()),annual_turnover=float(d.turnover.sum()/years),cost_dollars=float(d.cost_dollars.sum()),days=len(d))

def main():
    sel=json.loads((P/'selected_pair.json').read_text());assert sel['status']=='selected'
    names=[sel['pair']['a'],sel['pair']['b']];start=pd.Timestamp(sel['selection_cutoff']);raw={}
    spy=pd.read_parquet(R/'stock_overnight_2026_09/data/SPY.parquet');calendar=pd.to_datetime(spy.index.date);calendar=calendar[calendar>=start]
    for name,path in sel['source_paths'].items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==sel['source_sha256'][name]
        d=pd.read_parquet(path);d.index=pd.to_datetime(d.index.date);assert not d.index.duplicated().any();raw[name]=d.reindex(calendar)
    prices=pd.DataFrame({n:raw[n]['Adj Close'] for n in names});valid=(np.isfinite(prices)&(prices>0)).all(axis=1)
    missing=prices.loc[~valid].copy();missing.to_csv(P/'missing_selected_prices.csv')
    if not valid.iloc[0]:raise ValueError('Selected pair missing entry price; no replacement allowed')
    end_index=int(np.flatnonzero(~valid.to_numpy())[0]) if not valid.all() else len(prices)
    prices=prices.iloc[:end_index];assert len(prices)>1
    gross=(prices/prices.shift()).iloc[1:];dates=gross.index;years=(dates[-1]-start).days/365.25
    audits=[]
    for name in names:
        d=raw[name].loc[prices.index];alt=(d.Close+d.Dividends)/d.Close.shift();g=d['Adj Close']/d['Adj Close'].shift();diff=abs(g-alt)
        for date in d.index[diff>.005]:audits.append(dict(symbol=name,date=str(date.date()),adjusted_gross=float(g.loc[date]),close_dividend_gross=float(alt.loc[date]),difference=float(diff.loc[date])))
    pd.DataFrame(audits,columns=['symbol','date','adjusted_gross','close_dividend_gross','difference']).to_csv(P/'corporate_action_discrepancies.csv',index=False)
    for n in names:
        raw[n].loc[prices.index].to_parquet(P/f'input_{n}.parquet')
    gross.to_csv(P/'gross_returns.csv');prices.to_csv(P/'adjusted_prices.csv')
    G=gross.to_numpy();targets,U=universal(G,1001);_,fine=universal(G,10001)
    objective=lambda b:-float(np.log(b*G[:,0]+(1-b)*G[:,1]).sum())
    opt=minimize_scalar(objective,bounds=(0,1),method='bounded',options={'xatol':1e-13});assert opt.success
    best=min([0.,1.,float(opt.x)],key=objective)
    specifications={f'{names[0]}_buy_hold':(1.,None),f'{names[1]}_buy_hold':(0.,None),'pair_buy_hold_50_50':(.5,None),'daily_rebalance_50_50':(.5,np.full(len(G),.5)),'cover_universal_1001':(.5,targets),'hindsight_optimal_CRPortfolio':(best,np.full(len(G),best))}
    allrows=[];summary=[];curves={}
    for model,(w,tar) in specifications.items():
        for bps in COSTS:
            c=bps/10000;d=simulate(G,dates,w,c,tar);d['strategy']=model;d['cost_bps']=bps
            met=metrics(d,INITIAL,years);met['cost_dollars']+=INITIAL*c;met['annual_turnover']+=1/years
            summary.append(dict(strategy=model,cost_bps=bps,**met));allrows.append(d);curves[(model,bps)]=d
    daily=pd.concat(allrows,ignore_index=True);stats=pd.DataFrame(summary)
    benchmark=stats[stats.strategy=='pair_buy_hold_50_50'].set_index('cost_bps')
    stats['cagr_excess_vs_pair_hold']=stats.apply(lambda x:x.cagr-benchmark.loc[x.cost_bps,'cagr'],axis=1)
    stats['terminal_ratio_vs_pair_hold']=stats.apply(lambda x:x.terminal_wealth/benchmark.loc[x.cost_bps,'terminal_wealth'],axis=1)
    stats.to_csv(P/'summary.csv',index=False);daily.to_parquet(P/'daily.parquet',index=False)
    annual=[]
    for (model,bps),d in daily.groupby(['strategy','cost_bps']):
        previous=INITIAL
        for yr,g in d.groupby(d.date.dt.year):
            last=g.wealth.iloc[-1];annual.append(dict(strategy=model,cost_bps=bps,year=int(yr),return_net=float(last/previous-1)));previous=last
    pd.DataFrame(annual).to_csv(P/'annual.csv',index=False)
    logs=np.log(G);bonus=np.log(.5*G.sum(axis=1))-.5*logs.sum(axis=1)
    assert bonus.min()>-1e-12
    crp=curves[('daily_rebalance_50_50',0)].wealth.iloc[-1]/INITIAL
    weighted_growth=.5*logs.sum();identity=float(weighted_growth+bonus.sum())
    assert abs(np.log(crp)-identity)<1e-10
    assert np.allclose(curves[('cover_universal_1001',0)].wealth/INITIAL,U,rtol=1e-11)
    assert np.isclose(curves[('pair_buy_hold_50_50',0)].wealth.iloc[-1]/INITIAL,.5*np.prod(G[:,0])+.5*np.prod(G[:,1]))
    theo=73.619**(1/22)-1
    details=dict(names=names,start=str(start.date()),end=str(dates[-1].date()),years=years,days=len(G),full_requested_end=str(calendar[-1].date()),truncated=not bool(valid.all()),missing_dates=len(missing),corporate_action_discrepancies=len(audits),realized_vol={n:float(logs[:,i].std(ddof=1)*np.sqrt(252)) for i,n in enumerate(names)},realized_correlation=float(np.corrcoef(logs.T)[0,1]),hindsight_weight_a=best,gross_crp_multiple=crp,weighted_constituent_log_growth_per_year=float(weighted_growth/years),diversification_log_growth_per_year=float(bonus.sum()/years),crp_log_growth_per_year=float(identity/years),universal_grid_terminal_relative_error=float(U[-1]/fine[-1]-1),paper_22y_multiple=73.619,paper_example_cagr=theo,protocol_sha256=sel['protocol_sha256'])
    (P/'details.json').write_text(json.dumps(details,indent=2))
    print(json.dumps(details,indent=2));print(stats.to_string(index=False))
if __name__=='__main__':main()
