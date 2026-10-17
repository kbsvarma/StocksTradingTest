"""Research-only Mag7 evaluation. No brokerage or live-trading dependencies."""
from pathlib import Path
import pandas as pd, numpy as np, json, hashlib, platform
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
P=Path(__file__).resolve().parent
U=['AAPL','AMZN','GOOGL','META','MSFT','NVDA','TSLA']; ALL=U+['GOOG','QQQ','SPY']
D={s:pd.read_csv(P/'data'/f'{s}.csv',index_col=0,parse_dates=True) for s in ALL}
idx=D['SPY'].index
missing={s:len(idx.difference(d.index)) for s,d in D.items()}
C=pd.DataFrame({s:d['Adj Close'] for s,d in D.items()}).reindex(idx)
O=pd.DataFrame({s:d['Open']*d['Adj Close']/d['Close'] for s,d in D.items()}).reindex(idx)
PC=pd.DataFrame({s:d['Close'] for s,d in D.items()}).reindex(idx)
PO=pd.DataFrame({s:d['Open'] for s,d in D.items()}).reindex(idx)
DIV=pd.DataFrame({s:d['Dividends'] for s,d in D.items()}).reindex(idx).fillna(0)
assert (C.dropna()>0).all().all() and (O.dropna()>0).all().all()
# Last observed trading session of each *completed* calendar month only.
monthends=pd.Series(idx,index=idx).groupby(idx.to_period('M')).last()
monthends=monthends[monthends.index < idx[-1].to_period('M')]
M=C.loc[monthends.values].copy();M.index=monthends.index
PM=PC.loc[monthends.values].copy();PM.index=monthends.index
first=next(m for m in M.index[12:] if M.loc[m-12:m,U].notna().all().all())
SIGNALS=[m for m in M.index if m>=first]
start=idx.searchsorted(monthends.loc[first])+1
ix=idx[start:]; n=len(ix); syms=list(C.columns); k=len(syms); col={s:syms.index(s) for s in syms}
ca=C.loc[ix].to_numpy();oa=O.loc[ix].to_numpy();pc=PC.loc[ix].to_numpy();po=PO.loc[ix].to_numpy();dv=DIV.loc[ix].to_numpy()
assert np.isfinite(ca).all() and np.isfinite(oa).all()
rankrows=[]
def schedule(universe=U,windows=(3,6,12),raw=False,price=False,skip=0,top=1,positive=False,tie='12m',delay=0):
    table=PM if price else M; prev=None; events={};ties=0
    for m in SIGNALS:
        rr=pd.DataFrame({h:table.loc[m-skip,universe]/table.loc[m-h,universe]-1 for h in windows})
        ranks=rr.rank(ascending=False,method='average'); score=-rr.mean(axis=1) if raw else ranks.mean(axis=1)
        order=pd.DataFrame({'score':score,'r12':table.loc[m-skip,universe]/table.loc[m-12,universe]-1,'symbol':universe})
        tied=order[order.score==order.score.min()]
        ties+=len(tied)>1
        if tie=='ticker':order=order.sort_values(['score','symbol'])
        elif tie=='incumbent':
            order['old']=[0 if x==prev else 1 for x in order.index];order=order.sort_values(['score','old','r12','symbol'],ascending=[True,True,False,True])
        else:order=order.sort_values(['score','r12','symbol'],ascending=[True,False,True])
        chosen=list(order.index[:top]);prev=chosen[0]
        if positive and order.loc[chosen[0],'r12']<=0: chosen=[]
        j=idx.searchsorted(monthends.loc[m])+1+delay-start
        if j>=n:continue
        w=np.zeros(k)
        for s in chosen:w[col[s]]=1/len(chosen)
        events[j]=dict(w=w,signal=str(monthends.loc[m].date()),chosen=','.join(chosen) or 'CASH',tie=len(tied)>1)
        if universe==U and windows==(3,6,12) and not any([raw,price,skip,positive,delay]) and top==1 and tie=='12m':
            for s in universe:rankrows.append(dict(signal_date=str(monthends.loc[m].date()),execution_date=str(ix[j].date()),ticker=s,return_3m=rr.loc[s,3],return_6m=rr.loc[s,6],return_12m=rr.loc[s,12],rank_3m=ranks.loc[s,3],rank_6m=ranks.loc[s,6],rank_12m=ranks.loc[s,12],average_rank=score.loc[s],selected=s in chosen,composite_top_tie=len(tied)>1))
    return events,ties
BASE,ties=schedule()

def fixed_schedule(names,monthly=False):
    w=np.zeros(k)
    for s in names:w[col[s]]=1/len(names)
    keys=BASE.keys() if monthly else [0]
    return {j:dict(w=w.copy(),signal=BASE[j]['signal'],chosen=','.join(names),tie=False) for j in keys}

def run(events,cost=.001,close_exec=False,cash_div=False,tax=None):
    closes=pc if cash_div else ca; opens=po if cash_div else oa
    q=np.zeros(k);cash=100000.;lastw=None;values=[];returns=[];ledger=[];weights=[];prevval=100000.
    basis=0.;entry=None;carry=0.;taxes=0.;dollars_traded=0.
    for j in range(n):
        if cash_div:cash+=q@dv[j]
        if j in events:
            e=events[j];w=e['w']
            # Multi-stock portfolios rebalance monthly; a one-stock unchanged signal never churns.
            act=lastw is None or not np.array_equal(w,lastw) or (w>0).sum()>1
            if act:
                px=closes[j] if close_exec else opens[j]
                holdings=q*px;gross=holdings.sum()+cash;taxpaid=0.
                old=','.join(syms[a] for a in np.flatnonzero(q>0)) or 'CASH'
                if tax is not None and q.sum()>0:
                    proceeds=holdings.sum()*(1-cost);gain=proceeds-basis
                    if gain<0:carry-=gain
                    else:
                        taxable=max(0.,gain-carry);carry=max(0.,carry-gain)
                        rate=tax[1] if (ix[j]-entry).days>365 else tax[0]
                        taxpaid=taxable*rate
                    gross-=taxpaid;taxes+=taxpaid
                low,high=0.,gross
                for _ in range(45):
                    wealth=(low+high)/2
                    if wealth+cost*np.abs(wealth*w-holdings).sum()>gross:high=wealth
                    else:low=wealth
                wealth=(low+high)/2;target=wealth*w;traded=np.abs(target-holdings).sum();fees=cost*traded
                q=target/px;cash=wealth*(1-w.sum());dollars_traded+=traded
                if tax is not None and w.sum()>0:basis=wealth*(1+cost);entry=ix[j]
                ledger.append(dict(date=str(ix[j].date()),signal_date=e['signal'],old=old,new=e['chosen'],before=gross+taxpaid,after=wealth,friction=fees,traded=traded,turnover_fraction=traded/(gross+taxpaid),tax=taxpaid))
            lastw=w.copy()
        value=q@closes[j]+cash
        values.append(value);returns.append(value/prevval-1);prevval=value
        weights.append(q*closes[j]/value)
    return dict(values=np.array(values),r=np.array(returns),ledger=pd.DataFrame(ledger),weights=np.array(weights),taxes=taxes)

def stats(r,dates):
    r=np.asarray(r); v=np.r_[1.,np.cumprod(1+r)]; h=np.maximum.accumulate(v);dd=v/h-1
    years=((dates[-1]-dates[0]).days+1)/365.25
    vol=np.std(r,ddof=1)*np.sqrt(252);cagr=v[-1]**(1/years)-1
    bottom=int(np.argmin(dd));peak=int(np.argmax(v[:bottom+1]));recs=np.flatnonzero(v[bottom:]>=v[peak]);rec=int(bottom+recs[0]) if len(recs) else None
    ds=pd.Series(r,index=dates);mo=ds.groupby(ds.index.to_period('M')).apply(lambda x:np.prod(1+x)-1)
    duration=0;longest=0
    for d in dd:
        duration=duration+1 if d<-1e-12 else 0;longest=max(longest,duration)
    return dict(start=str(dates[0].date()),end=str(dates[-1].date()),years=years,cagr=cagr,ending_100k=v[-1]*100000,total_return=v[-1]-1,volatility=vol,sharpe_zero_rf=np.mean(r)*252/vol,max_drawdown=float(dd[bottom]),drawdown_peak=str(dates[max(0,peak-1)].date()),drawdown_trough=str(dates[max(0,bottom-1)].date()),drawdown_recovery=str(dates[max(0,rec-1)].date()) if rec else 'unrecovered',longest_underwater_sessions=longest,worst_day=r.min(),best_day=r.max(),worst_month=mo.min(),best_month=mo.max(),positive_month_fraction=(mo>0).mean(),month_count=len(mo))

runs={'Mag7 rank winner':run(BASE),'QQQ':run(fixed_schedule(['QQQ'])),'SPY':run(fixed_schedule(['SPY'])),'Mag7 equal monthly':run(fixed_schedule(U,True)),'Mag7 equal buy hold':run(fixed_schedule(U))}
for s in U:runs[s+' buy hold']=run(fixed_schedule([s]))
summary=pd.DataFrame([dict(name=name,**stats(x['r'],ix)) for name,x in runs.items()]);summary.to_csv(P/'summary.csv',index=False)
daily=pd.DataFrame({name:x['r'] for name,x in runs.items()},index=ix);daily.index.name='Date';daily.to_csv(P/'daily_returns.csv')
pd.DataFrame(rankrows).to_csv(P/'monthly_rankings.csv',index=False)
runs['Mag7 rank winner']['ledger'].to_csv(P/'rotations.csv',index=False)
monthly=(1+daily).groupby(daily.index.to_period('M')).prod()-1;monthly.to_csv(P/'monthly_returns.csv')
annual=(1+daily).groupby(daily.index.year).prod()-1;annual.to_csv(P/'annual_returns.csv')
periods=[('Full',ix[0],ix[-1]),('Early retrospective','2013-06-01','2019-12-31'),('2020-2022','2020-01-01','2022-12-31'),('Post June 2023','2023-06-01',ix[-1]),('2022 bear','2022-01-01','2022-12-31'),('2026 YTD','2026-01-01',ix[-1])]
sub=[]
for period,a,b in periods:
    mask=(ix>=pd.Timestamp(a))&(ix<=pd.Timestamp(b))
    for name,x in list(runs.items())[:5]:sub.append(dict(period=period,name=name,**stats(x['r'][mask],ix[mask])))
pd.DataFrame(sub).to_csv(P/'periods.csv',index=False)
# Restart every comparison with fresh $100,000 at each subperiod's first session.
# In particular, reset the equal-weight buy-and-hold benchmark rather than inheriting its 2013 weights.
fresh=[];fresh_monthly={}
for period,a,b in periods[1:]:
    mask=(ix>=pd.Timestamp(a))&(ix<=pd.Timestamp(b));jj=int(np.flatnonzero(mask)[0])
    current=BASE[max(j for j in BASE if j<=jj)]
    ev={j:e for j,e in BASE.items() if j>=jj};ev[jj]=current
    setups={'Mag7 rank winner':ev}
    for name,names,monthly_reset in [('QQQ',['QQQ'],False),('SPY',['SPY'],False),('Mag7 equal monthly',U,True),('Mag7 equal buy hold',U,False)]:
        w=np.zeros(k)
        for symbol in names:w[col[symbol]]=1/len(names)
        event=dict(w=w,signal=current['signal'],chosen=','.join(names),tie=False)
        setups[name]={j:event for j in ev} if monthly_reset else {jj:event}
    period_daily={}
    for name,e in setups.items():
        x=run(e);period_daily[name]=x['r'][mask];fresh.append(dict(period=period,name=name,**stats(x['r'][mask],ix[mask])))
    temp=pd.DataFrame(period_daily,index=ix[mask]);fresh_monthly[period]=(1+temp).groupby(temp.index.to_period('M')).prod()-1
pd.DataFrame(fresh).to_csv(P/'fresh_start_periods.csv',index=False)
fresh_monthly['Post June 2023'].to_csv(P/'fresh_post2023_monthly.csv')

rob=[]
for bps in [0,5,10,25,50]:
    for name,e in [('Mag7 rank winner',BASE),('QQQ',fixed_schedule(['QQQ'])),('Mag7 equal monthly',fixed_schedule(U,True))]:
        x=run(e,cost=bps/10000);rob.append(dict(test='cost',variant=name+f' {bps}bps',**stats(x['r'],ix)))
variants=[('Price-only signal',dict(price=True)),('3m only',dict(windows=(3,))),('6m only',dict(windows=(6,))),('12m only',dict(windows=(12,))),('Average raw returns',dict(raw=True)),('Skip recent month',dict(skip=1)),('Top 2',dict(top=2)),('Top 3',dict(top=3)),('12m positive else cash',dict(positive=True)),('Alphabetical tie',dict(tie='ticker')),('Incumbent tie',dict(tie='incumbent')),('Delay 1 session open',dict(delay=1)),('GOOG substitution',dict(universe=[s if s!='GOOGL' else 'GOOG' for s in U]))]
for name,kw in variants:
    e,_=schedule(**kw);x=run(e);rob.append(dict(test='rule',variant=name,**stats(x['r'],ix)))
for name,kw in [('Next session close',dict(close_exec=True)),('Cash dividends, no DRIP',dict(cash_div=True))]:
    x=run(BASE,**kw);rob.append(dict(test='execution',variant=name,**stats(x['r'],ix)))
for s in U:
    e,_=schedule(universe=[t for t in U if t!=s]);x=run(e);rob.append(dict(test='leave_one_out',variant='Exclude '+s,**stats(x['r'],ix)))
pd.DataFrame(rob).to_csv(P/'robustness.csv',index=False)

# Same-stock spells: actual open-to-open adjusted performance, excluding friction.
led=runs['Mag7 rank winner']['ledger'];spells=[]
for a,row in led.iterrows():
    j=ix.get_loc(pd.Timestamp(row.date));s=row['new'];c=col[s]
    if a+1<len(led):
        end=pd.Timestamp(led.iloc[a+1].date);z=ix.get_loc(end);price=oa[z,c];closed=True
    else:end=ix[-1];z=n-1;price=ca[z,c];closed=False
    spells.append(dict(ticker=s,entry=row.date,exit=str(end.date()),closed=closed,calendar_days=(end-pd.Timestamp(row.date)).days,gross_return=price/oa[j,c]-1))
spells=pd.DataFrame(spells);spells.to_csv(P/'holding_spells.csv',index=False)
selected=pd.Series([e['chosen'] for e in BASE.values()]);counts=selected.value_counts()
attr=[]
# Assign each close-to-close log growth to the stock named by the last execution (switch-day overnight is a small attribution convention).
winner=np.argmax(runs['Mag7 rank winner']['weights'],axis=1)
for s in U:
    mask=winner==col[s];attr.append(dict(ticker=s,signal_months=int(counts.get(s,0)),signal_fraction=float(counts.get(s,0)/len(selected)),sessions=int(mask.sum()),log_return_contribution=float(np.log1p(runs['Mag7 rank winner']['r'][mask]).sum())))
pd.DataFrame(attr).to_csv(P/'attribution.csv',index=False)
roll=[]
for months in [12,36,60]:
    complete_monthly=monthly[monthly.index < ix[-1].to_period('M')]
    z=(1+complete_monthly).rolling(months).apply(np.prod,raw=True)-1
    for name in list(runs)[:5]:
        s=z[name].dropna();roll.append(dict(months=months,name=name,count=len(s),min_return=s.min(),median_return=s.median(),max_return=s.max(),negative_fraction=(s<0).mean(),beat_qqq_fraction=(s>z.loc[s.index,'QQQ']).mean(),beat_equal_fraction=(s>z.loc[s.index,'Mag7 equal monthly']).mean()))
pd.DataFrame(roll).to_csv(P/'rolling.csv',index=False)

# Paired circular block bootstrap; excludes the incomplete terminal month.
mo=monthly.iloc[:-1] if ix[-1].day<28 else monthly
rng=np.random.default_rng(20260925);unc=[]
for label,sel in [('Full',np.ones(len(mo),bool)),('Post June 2023',mo.index>=pd.Period('2023-06','M'))]:
    z=mo.loc[sel] if label=='Full' else fresh_monthly['Post June 2023'].loc[lambda x:x.index<ix[-1].to_period('M')];N=len(z);block=6;B=10000
    starts=rng.integers(0,N,size=(B,int(np.ceil(N/block))));ids=((starts[:,:,None]+np.arange(block))%N).reshape(B,-1)[:,:N]
    for bench in ['QQQ','Mag7 equal monthly','Mag7 equal buy hold']:
        dif=np.log1p(z['Mag7 rank winner'].to_numpy())-np.log1p(z[bench].to_numpy())
        boot=dif[ids].mean(axis=1)*12
        unc.append(dict(period=label,benchmark=bench,months=N,annualized_log_relative_return=dif.mean()*12,ci_low=float(np.quantile(boot,.025)),ci_high=float(np.quantile(boot,.975)),bootstrap_fraction_positive=float((boot>0).mean())))
pd.DataFrame(unc).to_csv(P/'uncertainty.csv',index=False)

# Random monthly single-stock choices, exactly the same next-open calendar and friction; vectorized daily accounting.
B=10000;rng=np.random.default_rng(777);choices=rng.integers(0,len(U),size=(len(BASE),B));pos=np.zeros(B,dtype=int);v=np.full(B,100000.);pclose=None;m=0
for j in range(n):
    if j in BASE:
        nxt=choices[m];m+=1
        if j==0:v/=1.001
        else:
            v*=oa[j,pos]/pclose
            changed=nxt!=pos;v[changed]*=.999/1.001
        pos=nxt;v*=ca[j,pos]/oa[j,pos]
    else:v*=ca[j,pos]/pclose
    pclose=ca[j,pos]
years=summary.iloc[0].years;random_cagr=(v/100000)**(1/years)-1
random=dict(trials=B,seed=777,median_cagr=float(np.median(random_cagr)),p05_cagr=float(np.quantile(random_cagr,.05)),p95_cagr=float(np.quantile(random_cagr,.95)),strategy_percentile=float((v<runs['Mag7 rank winner']['values'][-1]).mean()),caveat='Conditional historical random-choice comparison, not a future-return probability or a data-mining-adjusted p-value.')

# Correlation, beta, and illustrative constant-weight portfolio blends, monthly rebalance ignoring incremental blend friction.
fits=[]
for bench in ['QQQ','SPY','Mag7 equal monthly']:
    a=daily['Mag7 rank winner'];b=daily[bench]
    for w in [0,.05,.1,.2,1]:
        r=(1-w)*monthly[bench]+w*monthly['Mag7 rank winner'];v=(1+r).cumprod();yrs=len(r)/12
        fits.append(dict(base=bench,sleeve_weight=w,monthly_cagr=v.iloc[-1]**(1/yrs)-1,monthly_vol=r.std()*np.sqrt(12),monthly_max_drawdown=(v/np.maximum.accumulate(np.r_[1,v]) [1:]-1).min(),correlation_daily=a.corr(b),beta_daily=np.cov(a,b,ddof=1)[0,1]/np.var(b,ddof=1)))
pd.DataFrame(fits).to_csv(P/'portfolio_fit.csv',index=False)

# Illustrative tax drag: immediate tax on positive realized gains, loss carryforward, no refund; total-return units conflate dividends.
# It is a tax sensitivity, NOT a tax return calculation. Rates only; cash-dividend and wash-sale reality are not simulated.
taxrows=[]
for rates in [(0.,0.),(.24,.15),(.37,.20)]:
    for name,e in [('Mag7 rank winner',BASE),('QQQ',fixed_schedule(['QQQ'])),('SPY',fixed_schedule(['SPY']))]:
        x=run(e,tax=rates);taxrows.append(dict(name=name,short_rate=rates[0],long_rate=rates[1],tax_paid=x['taxes'],**stats(x['r'],ix)))
pd.DataFrame(taxrows).to_csv(P/'tax_sensitivity.csv',index=False)

# Independent one-stock recurrence validates the share-ledger implementation including switch overnight.
v=100000.;held=None;ref=[]
for j in range(n):
    if j in BASE:
        target=col[BASE[j]['chosen']]
        if held is None:v/=1.001
        else:
            v*=oa[j,held]/ca[j-1,held]
            if target!=held:v*=.999/1.001
        held=target;v*=ca[j,held]/oa[j,held]
    else:v*=ca[j,held]/ca[j-1,held]
    ref.append(v)
err=float(np.max(np.abs(np.array(ref)/runs['Mag7 rank winner']['values']-1)))
assert err<1e-9,err
assert all(pd.Timestamp(e['signal'])<ix[j] for j,e in BASE.items())
assert len(led)==1+sum(a!=b for a,b in zip(selected.iloc[1:],selected.iloc[:-1]))
# Export corporate action audit and large daily moves for manual review.
actions=[]
for s,d in D.items():
    for date,row in d[(d['Stock Splits']!=0)|(d['Dividends']!=0)].iterrows():actions.append(dict(ticker=s,date=str(date.date()),split=row['Stock Splits'],dividend=row['Dividends']))
pd.DataFrame(actions).to_csv(P/'corporate_actions.csv',index=False)
moves=C.pct_change(fill_method=None).stack();moves[moves.abs()>.3].rename('return').to_csv(P/'large_moves.csv')
meta=dict(data_start=str(idx[0].date()),data_end=str(idx[-1].date()),missing_sessions_before_common_start=missing,first_signal=str(monthends.loc[first].date()),first_trade=str(ix[0].date()),months=len(BASE),top_composite_ties=int(ties),switches=len(led)-1,switches_per_year=(len(led)-1)/years,friction_dollars=float(led.friction.sum()),turnover_annual_two_sided=float(led.turnover_fraction.sum()/years),independent_ledger_relative_error=err,random=random,python=platform.python_version(),script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
(P/'diagnostics.json').write_text(json.dumps(meta,indent=2))
fig,axes=plt.subplots(2,1,figsize=(12,8),sharex=True,gridspec_kw={'height_ratios':[2,1]})
colors=['#dc5a26','#2166ac','#666666','#218c74','#8a62a4']
for (name,x),color in zip(list(runs.items())[:5],colors):
    axes[0].plot(ix,x['values'],label=name,color=color,lw=1.5)
    peak=np.maximum.accumulate(np.r_[100000,x['values']])[1:];axes[1].plot(ix,100*(x['values']/peak-1),color=color,lw=1)
axes[0].set_yscale('log');axes[0].set_ylabel('$100,000 growth (log scale)');axes[0].legend(loc='upper left',fontsize=9)
axes[1].set_ylabel('Drawdown (%)');axes[0].set_title('Mag7 monthly rank winner: historical research, 10 bps per side, before tax')
for ax in axes:ax.grid(alpha=.2)
fig.tight_layout();fig.savefig(P/'performance.png',dpi=160);plt.close(fig)
print(summary.head(5).to_string(index=False));print(json.dumps(meta,indent=2));print(pd.DataFrame(unc).to_string(index=False))
