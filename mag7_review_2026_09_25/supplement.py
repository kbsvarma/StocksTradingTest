"""Additional validation using saved input files and outputs; does not rerun parameter search."""
from pathlib import Path
import pandas as pd,numpy as np,json
P=Path(__file__).resolve().parent
U=['AAPL','AMZN','GOOGL','META','MSFT','NVDA','TSLA'];D={s:pd.read_csv(P/'data'/f'{s}.csv',index_col=0,parse_dates=True) for s in U}
r=pd.read_csv(P/'daily_returns.csv',index_col=0,parse_dates=True);ix=r.index
C=pd.DataFrame({s:d['Adj Close'] for s,d in D.items()}).loc[ix].to_numpy();O=pd.DataFrame({s:d['Open']*d['Adj Close']/d['Close'] for s,d in D.items()}).loc[ix].to_numpy()
ledger=pd.read_csv(P/'rotations.csv');switch={ix.get_loc(pd.Timestamp(x.date)):U.index(x['new']) for _,x in ledger.iterrows()};years=((ix[-1]-ix[0]).days+1)/365.25
# Repeat random choices with EXACT SAME switch dates and no consecutive repeat.
B=10000;rng=np.random.default_rng(779);v=np.full(B,100000.);pos=None;prev=None
for j in range(len(ix)):
    if j in switch:
        if pos is None:pos=rng.integers(0,7,B);v/=1.001
        else:
            v*=O[j,pos]/prev;v*=.999/1.001
            draw=rng.integers(0,6,B);pos=draw+(draw>=pos)
        v*=C[j,pos]/O[j,pos]
    else:v*=C[j,pos]/prev
    prev=C[j,pos]
a=(v/100000)**(1/years)-1;strat=100000*(1+r['Mag7 rank winner']).prod()
result=dict(trials=B,seed=779,median_cagr=float(np.median(a)),p05_cagr=float(np.quantile(a,.05)),p95_cagr=float(np.quantile(a,.95)),strategy_percentile=float((v<strat).mean()))
# Cash settlement illustration: sell on prescribed day, buy next session open.
value=100000.;pos=None;pending=None;curve=[]
for j in range(len(ix)):
    if pending is not None:
        pos=pending;pending=None;value/=1.001;value*=C[j,pos]/O[j,pos]
    elif j in switch:
        target=switch[j]
        if pos is None:
            pos=target;value/=1.001;value*=C[j,pos]/O[j,pos]
        else:
            value*=O[j,pos]/C[j-1,pos];value*=.999;pos=None;pending=target
    elif pos is not None:value*=C[j,pos]/C[j-1,pos]
    curve.append(value)
curve=np.array(curve);dd=curve/np.maximum.accumulate(np.r_[100000.,curve])[1:]-1
result['cash_settlement_next_day_buy']=dict(cagr=float((curve[-1]/100000)**(1/years)-1),ending_100k=float(curve[-1]),max_drawdown=float(dd.min()),note='Requires relaxing first-trading-day-only restriction; not the screenshot strategy.')
# Exposure drift of sleeve against QQQ, with no transfers or top-ups after initial funding.
fits=[]
for base in ['QQQ','SPY','Mag7 equal monthly']:
    a=(1+r['Mag7 rank winner']).cumprod();b=(1+r[base]).cumprod()
    for w in [.05,.10,.20,.40]:
        v=w*a+(1-w)*b;ret=v.pct_change();ret.iloc[0]=v.iloc[0]-1;weights=w*a/v
        fits.append(dict(base=base,initial_sleeve_weight=w,ending_sleeve_weight=weights.iloc[-1],max_sleeve_weight=weights.max(),cagr=v.iloc[-1]**(1/years)-1,volatility=ret.std()*np.sqrt(252),max_drawdown=(v/np.maximum.accumulate(np.r_[1.,v])[1:]-1).min()))
pd.DataFrame(fits).to_csv(P/'no_topup_portfolio_fit.csv',index=False)
# Snapshot hashes for all inputs. Validate cached raw close/action accounting.
# Adj-close returns versus independent Close+dividend return calculation.
audit=[]
for s,d in D.items():
    total=(d.Close+d.Dividends)/d.Close.shift()-1
    adj=d['Adj Close'].pct_change();diff=(total-adj).dropna()
    audit.append(dict(ticker=s,max_abs_return_difference=float(diff.abs().max()),over_10bps=int((diff.abs()>.001).sum()),split_count=int((d['Stock Splits']!=0).sum())))
pd.DataFrame(audit).to_csv(P/'adjustment_audit.csv',index=False)
(P/'supplement.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));print(pd.DataFrame(fits).to_string(index=False));print(pd.DataFrame(audit).to_string(index=False))
