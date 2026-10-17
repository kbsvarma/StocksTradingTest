"""Summaries with trading-day clustered uncertainty; no model selection."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
P=Path(__file__).resolve().parent;D=P/'results'

def ci_cluster(df,value,seed=20260912):
    q=df.dropna(subset=[value]).groupby('day')[value].agg(['sum','count'])
    if len(q)<2:return (np.nan,np.nan)
    rng=np.random.default_rng(seed);ix=rng.integers(0,len(q),size=(2000,len(q)))
    means=q['sum'].to_numpy()[ix].sum(axis=1)/q['count'].to_numpy()[ix].sum(axis=1)
    return tuple(np.quantile(means,[.025,.975]))

def table(df):return df.to_markdown(index=False,floatfmt='.3f')

def main():
    p=pd.read_parquet(D/'predictions.parquet');f=pd.read_parquet(D/'fits.parquet');t=pd.read_parquet(D/'trades.parquet');a=pd.read_parquet(D/'audit.parquet')
    for d in [p,f,t,a]:d['year']=d.day.str[:4]
    p['abs_error']=abs(p.error);p['inside']=(p.pred>=p.bid)&(p.pred<=p.ask)
    p['spread_error']=p.abs_error/np.maximum(p.ask-p.bid,.2)
    keys=['day','time','fold','cp','K']
    # Exactly matched held-out contracts for all three models.
    wide=p.pivot(index=keys,columns='model',values='abs_error').dropna()
    common=p.set_index(keys).loc[wide.index].reset_index()
    pricing=[]
    for year in ['ALL']+sorted(p.year.unique()):
        sub=common if year=='ALL' else common[common.year==year]
        for m,g in sub.groupby('model'):
            daily=g.groupby('day').agg(mae=('abs_error','mean'),inside=('inside','mean'),spread_error=('spread_error','mean'))
            pricing.append(dict(period=year,model=m,days=len(daily),contracts=len(g),mae_dollars=daily.mae.mean()*100,inside_pct=daily.inside.mean()*100,error_spreads=daily.spread_error.mean()))
    pricing=pd.DataFrame(pricing);pricing.to_csv(D/'pricing_summary.csv',index=False)
    paired=[]
    for year in ['ALL']+sorted(p.year.unique()):
        q=wide.reset_index();q=q if year=='ALL' else q[q.day.str[:4]==year]
        for m in ['ssvi','svi']:
            z=q.assign(improvement=(q['flat']-q[m])*100).groupby('day',as_index=False).improvement.mean()
            lo,hi=ci_cluster(z,'improvement');paired.append(dict(period=year,model=m,mae_improvement_dollars=z.improvement.mean(),ci_low=lo,ci_high=hi,days=len(z)))
    paired=pd.DataFrame(paired);paired.to_csv(D/'paired_pricing.csv',index=False)
    executed=t[t.status!='missing_entry'].copy();stats=[]
    for year in ['ALL']+sorted(t.year.unique()):
        sub=executed if year=='ALL' else executed[executed.year==year]
        for (m,h),g in sub.groupby(['model','horizon']):
            lo,hi=ci_cluster(g,'pnl');conv=g.residual_convergence.dropna() if 'residual_convergence' in g else pd.Series(dtype=float)
            stats.append(dict(period=year,model=m,horizon=h,days=g.day.nunique(),trades=len(g),mean_net=g.pnl.mean(),ci_low=lo,ci_high=hi,total_net=g.pnl.sum(),stress_mean=g.pnl_stress.mean(),win_pct=(g.pnl>0).mean()*100,missing_exits=(g.status!='ok').sum(),mean_mid_pnl=g.mid_pnl.mean(),convergence_n=len(conv),mean_residual_convergence=conv.mean()))
    stats=pd.DataFrame(stats);stats.to_csv(D/'trading_summary.csv',index=False)
    if len(executed):
        executed['month']=executed.day.str[:6]
        executed.groupby(['month','model','horizon']).agg(trades=('pnl','size'),mean_net=('pnl','mean'),total_net=('pnl','sum')).to_csv(D/'monthly_pnl.csv')
    fitstatus=f.groupby(['model','status']).size().rename('count').reset_index();fitstatus.to_csv(D/'fit_status.csv',index=False)
    primary=stats[(stats.period=='ALL')&(stats.horizon==15)]
    yearly=stats[(stats.period!='ALL')&(stats.horizon==15)]
    text=f'''# SPXW smile pricing and convergence pilot

Completed local-only research, not a live strategy recommendation. Rules in PROTOCOL.md.
Numerical convergence refinement is disclosed in NUMERICAL_REFINEMENT.md: the
authoritative run increases the optimizer limit from 180 to 1,200 iterations.
The initial run and its code are preserved; economic rules were unchanged.

## Findings
On {len(wide):,} matched held-out observations across {common.day.nunique()} days,
SVI's mean day-level pricing error was
${pricing[(pricing.period=='ALL')&(pricing.model=='svi')].mae_dollars.iloc[0]:.2f}
per option contract, versus
${pricing[(pricing.period=='ALL')&(pricing.model=='flat')].mae_dollars.iloc[0]:.2f}
for flat volatility. All three models' primary 15-minute spread trades had
negative mean net P&L. SVI produced only
{int(primary[primary.model=='svi'].trades.iloc[0])} qualifying primary trades,
so its economic result is especially small-sample.

SVI still rejected {int(((f.model=='svi')&(f.status!='ok')).sum())} of
{int((f.model=='svi').sum())} attempted calibrations
({100*((f.model=='svi')&(f.status!='ok')).sum()/(f.model=='svi').sum():.1f}%).
The pricing comparison is conditional on successful calibration, not full-universe
coverage. The extended numerical budget improves acceptance but does not remove
this limitation. This pilot supports smile interpolation; it does not establish
a profitable convergence strategy.

## Coverage
- {len(a)} local day-files considered, {a.day.min()}–{a.day.max()}.
- {p.day.nunique()} days with predictions; {len(p):,} model/contract predictions.
- {len(wide):,} held-out contract observations shared by all three models.
- {int(a.raw_rows.sum()):,} source rows read; {int(a.duplicate_rows.sum()):,} overlapping rows deduplicated;
  {int(a.wrong_expiry.sum()):,} wrong-expiry/symbol rows removed; {int(a.invalid_rows.sum()):,} invalid quote rows removed.
- {int((t.status=='missing_entry').sum())} signal/horizon rows lacked the exact next-minute entry.
  Counts across horizons refer to repeated signals, not independent opportunities.

## Held-out pricing
Equal weight per trading day; only contracts successfully predicted by all models.
Dollar errors use the SPX option multiplier of 100. Error/spread uses a 0.20-point
minimum denominator. Inside-market fraction compares predicted value with bid/ask.

{table(pricing)}

Paired improvement over flat volatility: positive is better. Confidence intervals
resample trading days, not correlated strikes/minutes.

{table(paired)}

## Trading: primary 15-minute horizon
One 10-point vertical per model/snapshot when the preregistered discrepancy threshold
is met. Next-minute entry, bid/ask crosses at entry and exit, $2.60 round-trip fees.
The $20 additional stress cost represents 5 cents per leg per transaction.
Different models may select different spreads and trade counts.

{table(primary[['model','days','trades','mean_net','ci_low','ci_high','total_net','stress_mean','win_pct','missing_exits']])}

By year (historically reused research data, not untouched holdouts):

{table(yearly[['period','model','trades','mean_net','ci_low','ci_high','stress_mean']])}

## Secondary horizons

{table(stats[stats.period=='ALL'][['model','horizon','trades','mean_net','ci_low','ci_high','stress_mean']])}

## Midpoint and residual diagnostics
Midpoint P&L excludes spreads and fees and is not executable. Residual convergence
is signed entry model discrepancy minus exit discrepancy after independently
refitting the same training-strike fold. It is diagnostic, not the actual cashflow.
Only supported, successfully refitted exits enter the convergence statistic.

{table(primary[['model','mean_mid_pnl','convergence_n','mean_residual_convergence']])}

## Verification
An independent raw-file reconciliation is recorded in results/verification.json.
Synthetic checks cover put-call parity, intrinsic bounds, analytic density derivatives,
held-out-strike isolation, recovery of flat-volatility synthetic prices, and cashflows.

## Fit/coverage failures

{table(fitstatus)}

## Interpretation and limits
A lower held-out pricing error demonstrates cross-sectional interpolation quality,
not an independent forecast of option value. A market-fitted smile can reproduce
market prices without predicting profitable changes. This unhedged vertical test
mixes convergence with delta exposure, theta, and changing volatility.

SSVI uses sufficient butterfly constraints for an individual slice. Raw SVI uses
positive minimum variance, Lee wing bounds, optimization-grid constraints and a
denser post-fit density screen on log-moneyness [-0.15, 0.15]; this is not a global
arbitrage proof. No multi-expiry surface or calendar-arbitrage test was performed.

Forward estimates use training-strike parity with discount factor one, not
independent SPX spot. Original strike acquisition was restricted and can bias
eligibility. Repairs and call wings are included. Missing future quotes are not
silently removed: adverse payoff-bound liquidation is included in primary P&L.
No forward filling, tick fills, queue assumptions, or settlement-price proxies.
Minute snapshots do not verify quote age or simultaneous executable leg prices.
Fee/slippage settings are research assumptions. Day bootstrap does not fully
account for multi-day regime dependence; secondary comparisons are exploratory.
No profitable model was selected or deployed automatically.

## Reproduce
`/opt/anaconda3/bin/python spx_smile_pricing_2026_09/run.py --selftest`

`/opt/anaconda3/bin/python spx_smile_pricing_2026_09/run.py --workers 4`

`/opt/anaconda3/bin/python spx_smile_pricing_2026_09/analyze.py`

Sources: [Gatheral–Jacquier](https://arxiv.org/abs/1204.0646),
[Databento CBBO](https://databento.com/docs/schemas-and-data-formats/cbbo).
Source file sizes/timestamps and script/protocol hashes are in results/manifest.json.
Raw predictions, calibration parameters, trade cashflows, audit counts, and monthly
summaries are retained in results/ for independent inspection.
'''
    (P/'REPORT.md').write_text(text)
    print(table(pricing[pricing.period=='ALL']));print(table(primary));print('REPORT:',P/'REPORT.md')
if __name__=='__main__':main()
