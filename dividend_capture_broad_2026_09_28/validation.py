from portfolio import *
# Select an asset's timing using returns ending BEFORE 2025, then freeze for the entire 2025+ period.
selected=[]
for s in SYMS:
 if CAT[s]=='benchmark':continue
 train=E[(E.ticker==s)&(E.j<DATES.searchsorted('2025-01-01'))]
 score=train.groupby(['before','after']).net_return.agg(['mean','count']);score=score[score['count']>=12]
 if score.empty:continue
 b,a=score.sort_values(['mean'],ascending=False).index[0]
 row=simulate([s],int(b),int(a),start_date='2025-01-02',keep=f'{s}_frozen2025')
 bh=simulate([s],start_date='2025-01-02',buyhold=True)
 selected.append(dict(ticker=s,category=CAT[s],training_events=int(score.loc[(b,a),'count']),training_mean=score.loc[(b,a),'mean'],benchmark_ending=bh['ending_value'],**row))
pd.DataFrame(selected).to_csv(P/'frozen2025_validation.csv',index=False)
wf=pd.read_csv(P/'walkforward_events.csv');rows=[]
for cat,gg in wf.groupby('category'):
 schedule={int(r.test_year):(int(r.before),int(r.after)) for r in gg.itertuples()}
 row=simulate(U[cat],start_date=f'{min(schedule)}-01-01',slots=4,schedule=schedule,allow_new=True,liquidity=True,keep=f'walkforward_{cat}')
 row['before']=-1;row['after']=-1
 rows.append(dict(category=cat,selection_years=len(schedule),**row))
pd.DataFrame(rows).to_csv(P/'walkforward_portfolios.csv',index=False)
# Timing placebo: shift each calendar-year ownership mask circularly; preserve time invested.
# This uses a common instantaneous-total-return convention for observed AND placebo paths.
# It diagnoses timing, not cash settlement or an executable randomized portfolio.
placebo=[];rng=np.random.default_rng(8392);R=2000
for s in SYMS:
 if CAT[s]=='benchmark':continue
 for b,a in [(1,1),(5,5)]:
  tag=f'{s}_common2025_b{b}_a{a}'
  path=P/'daily'/f'{tag}.csv'
  if not path.exists():continue
  h=pd.read_csv(path,index_col=0,parse_dates=True);idx=DATES.get_indexer(h.index);ownership=h.invested.to_numpy()/h.equity.to_numpy()
  market=DATA[s]['total'][idx]
  def score(mask):
   prev=np.concatenate([np.zeros((*mask.shape[:-1],1)),mask[...,:-1]],axis=-1)
   # Charge entry/exit costs; shifts are an exposure-matched diagnostic, not a trading rule.
   turnover=np.where((mask>0)&(prev==0),mask,0)+np.where((mask==0)&(prev>0),prev,0)
   factor=(1+prev*market)*(1-.0005*turnover)
   return np.log(factor).sum(axis=-1)
  actual=float(score(ownership)); shifted=np.empty((R,len(h)))
  for year in np.unique(h.index.year):
   j=np.flatnonzero(h.index.year==year);shift=rng.integers(len(j),size=R);ix=(np.arange(len(j))[None,:]-shift[:,None])%len(j)
   shifted[:,j]=ownership[j][ix]
  val=score(shifted);pval=(1+(val>=actual).sum())/(R+1)
  placebo.append(dict(ticker=s,category=CAT[s],before=b,after=a,observed_proxy_return=np.expm1(actual),placebo_median=np.expm1(np.median(val)),placebo_low=np.expm1(np.quantile(val,.025)),placebo_high=np.expm1(np.quantile(val,.975)),one_sided_p=pval))
pd0=pd.DataFrame(placebo);order=pd0.one_sided_p.sort_values().index;m=len(pd0);raw=pd0.loc[order,'one_sided_p'].to_numpy()*m/np.arange(1,m+1);q=np.minimum.accumulate(raw[::-1])[::-1];pd0.loc[order,'fdr_q']=np.minimum(q,1)
pd0.to_csv(P/'timing_placebo.csv',index=False)
# Comprehensive hash + cash-flow + issuer checks. Recompute saved ledgers independently.
import hashlib
man=json.loads((P/'manifest.json').read_text());checks=[]
for row in man['files']:
 assert row.get('error') is None
 assert hashlib.sha256((P/'data'/f"{row['ticker']}.csv").read_bytes()).hexdigest()==row['sha256']
for s in OV:
 div=DATA[s]['div'];got=np.flatnonzero(div>0);expected=np.array([DATES.get_loc(pd.Timestamp(x)) for x in OV[s].ex_date])
 assert set(got)==set(expected),(s,'unmatched or duplicate payouts')
for f in sorted((P/'ledgers').glob('*.csv')):
 if 'buyhold' in f.stem:continue
 l=pd.read_csv(f);d=pd.read_csv(P/'daily'/f.name)
 expected=100000+l.pnl.sum();err=expected-d.equity.iloc[-1]
 assert abs(err)<1e-5
 # Never hold overlapping positions in the same asset; same-session exit/reentry would be allowed.
 for s,g in l.groupby('ticker'):
  g=g.sort_values('entry_i');assert (g.entry_i.to_numpy()[1:]>=g.exit_i.to_numpy()[:-1]).all()
 assert (d.cash>=-1e-6).all() and (d.invested<=d.equity+1e-6).all()
 checks.append(dict(path=f.stem,trades=len(l),ending=d.equity.iloc[-1],cashflow_error=err))
pd.DataFrame(checks).to_csv(P/'reconciliation.csv',index=False)
# Annual summaries and reportable periods for every saved daily curve.
years=[]
for f in (P/'daily').glob('*.csv'):
 d=pd.read_csv(f,index_col=0,parse_dates=True)
 for y,g in d.groupby(d.index.year):years.append(dict(path=f.stem,year=y,return_total=(1+g.return_daily).prod()-1))
pd.DataFrame(years).to_csv(P/'annual_returns.csv',index=False)
print('Frozen selections',len(selected),'walk-forward pools',len(rows),'timing placebo tests',len(pd0),'ledgers reconciled',len(checks),'FDR q<.05',int((pd0.fdr_q<.05).sum()),flush=True)
print(pd.DataFrame(selected).sort_values('CAGR',ascending=False)[['ticker','before','after','training_mean','ending_value','benchmark_ending','CAGR','max_drawdown']].head(12).round(5).to_string(index=False),flush=True)
print(pd.DataFrame(rows)[['category','start','ending_value','CAGR','max_drawdown','trades']].round(5).to_string(index=False),flush=True)
print(pd0[pd0.ticker.isin(['QQQI','SPYI','JEPI','JEPQ'])].round(5).to_string(index=False),flush=True)
