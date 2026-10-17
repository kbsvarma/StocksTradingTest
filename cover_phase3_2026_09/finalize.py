from pathlib import Path
import pandas as pd,numpy as np,json,hashlib
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
P=Path(__file__).resolve().parent
x=pd.read_csv(P/'round_g_comparisons.csv');c=x[(x.a=='CI')&(x.capital==100000)&(x.design=='weekly_entry')];c.to_csv(P/'CANDIDATE_RESULTS.csv',index=False)
spec={'status':'research prototype; freeze for forward observation, no production promotion','primary_stock_pair':['CI','YUMC'],'initial_account_usd':100000,'initial_core_budget':.5,'initial_stock_budget':.5,'core_reference_capital_usd':50000,'core_policy':'archived baseline Donchian20 K2 weekly rotation; original signals/stops preserved; Ridge/WIDTH20 sensitivity only','inner_stock_rule':'daily 50/50, funded fractional shares','outer_rule':'weekly first stock-session close; desired core dollars from preceding completed close; transfer capital, preserve open core positions; update size at next original entry','core_sizing':'round to nearest whole micro; virtual reference signal state retained even if actual size zero','collateral_guard':'30% of futures notional for entry sizing and outward core transfers; research stress proxy, not actual broker margin','signals_use_future_data':False,'sample':'2018-05-01 through 2026-07-02; all historical and already inspected','readiness':'No deployment. Stronger than matching static blend, not a demonstrated superior replacement for core alone.'}
(P/'candidate.json').write_text(json.dumps(spec,indent=2))
def pc(z):return f'{z*100:.2f}%'
def tab(head,rows):return '| '+' | '.join(head)+' |\n| '+' | '.join(['---']*len(head))+' |\n'+'\n'.join('| '+' | '.join(map(str,r))+' |' for r in rows)
def link(f,label=None):return f'[{label or f}]({P/f})'
core=pd.read_csv(P/'unchanged_core_100k.csv');b=c[(c.parent=='baseline')&(c.case=='stress')&(c.period=='full')].iloc[0];a=core[(core.parent=='baseline')&(core.case=='stress')&(core.period=='full')].iloc[0]
summary=tab(['Design, $100k start','CAGR','Worst marked drawdown','Sharpe'],[['Unchanged archived core',pc(a.cagr),pc(-a.maxdd),f'{a.sharpe:.2f}'],['Same core/stock blend, no outer transfers',pc(b.static_cagr),pc(-b.static_maxdd),f'{b.static_sharpe:.2f}'],['Weekly capital controller + CI/YUMC',pc(b.cagr),pc(-b.maxdd),f'{b.sharpe:.2f}']])
checks=tab(['Core','Cost case','Controller CAGR','Static blend CAGR','Controller DD','Static blend DD'],[[r.parent,r.case,pc(r.cagr),pc(r.static_cagr),pc(-r.maxdd),pc(-r.static_maxdd)] for r in c[c.period=='full'].itertuples()])
ci=pd.read_csv(P/'family_uncertainty.csv');q=ci[ci.candidate=='baseline_stress_CI_YUMC__weekly_entry'].iloc[0]
counts={f.stem:len(pd.read_csv(f)) for f in P.glob('round_?_scores.csv')};(P/'RUN_COUNTS.json').write_text(json.dumps(counts,indent=2))
text=f'''**Phase 3 — a concrete capital-rebalancing integration**

**My decision:** retain the weekly capital controller as a research prototype, with CI/YUMC as its primary mechanism-validation pair. It demonstrates a repeatable historical improvement over the same blend without capital transfers. **Do not promote it as an upgrade to the trend bot yet:** its risk-adjusted return is weaker than the unchanged core alone. This distinction determines the recommendation; finding a positive rebalancing effect is not the same as improving the entire bot.

Seven connected research rounds produced {sum(counts.values()):,} configuration/period/cost/capital scenarios. These are not independent experiments. The original Phase 1–2 files and production bot were not changed. No new data were purchased and no orders were placed.

**The implemented design**

Start the research account at $100,000: $50,000 collateral/capital assigned to one archived core unit and $50,000 to CI/YUMC, initially $25,000 each. The stock sleeve rebalances daily to50/50. Each week's first stock-session close uses the preceding completed close's desired core dollar budget to transfer capital between sleeves. A relative winner supplies capital to the other sleeve.

Core breakout signals and trailing exits remain the original ones. Open trend positions are never trimmed or enlarged by the controller. Revised allocation changes the quantity of the next original core entry, rounded to whole micro contracts. Therefore capital budgets can rebalance while actual market exposures temporarily drift. The replay retains a virtual reference signal state even when rounding gives zero actual contracts; changing that behavior would be a different strategy.

A30%-of-notional research collateral guard limits new core entries and transfers out of core. It is a stress assumption, not a verified historical broker margin schedule or a guarantee against margin calls. No borrowing finances stock purchases. The frozen specification is in {link('candidate.json')} and the runnable replay is {link('round_c.py')}, exercised by {link('round_g.py')}.

**What the completed replay shows**

May1,2018–July2,2026, same dates and $100,000 starting account. Stocks use10bp per dollar traded; futures use$7 per micro round trip plus one adverse tick per fill. All returns are pre-tax, with zero interest on cash. The stock data incorporate dividend adjustments.

{summary}

The controller adds about{100*(b.cagr-b.static_cagr):.2f} percentage points annualized over the identical static blend, and reduces that blend's worst marked drawdown by{100*(b.maxdd-b.static_maxdd):.2f} percentage points. That is the incremental capital-rebalancing result. Compared with the unchanged bot, the extra stock exposure raises both return and drawdown; it does not improve Sharpe. I would not conceal that by showing only the static-blend comparison.

“Unchanged core” retains one archived core unit on the full$100k account, including its idle collateral. An additional normalized capital-allocation comparator in earlier tables devotes$50k per core unit, meaning a100%-core$100k allocation scales the archived core by two. That scaled comparator must not be called the unchanged bot. Neither comparator includes the gold sleeve or breadth satellite: the replay covers NQ/ES/YM core only, not the complete deployed system.

The same architecture was checked with existing Ridge and WIDTH20 research cores. Those are sensitivity comparisons, not a recommendation to switch the production profile.

{checks}

The severe case uses$14 round trip plus two adverse ticks for futures and25bp per stock dollar traded. For this100k candidate, the controller improves CAGR and drawdown over its matched static blend across full history,2018–2021 and2022–2025 for all three cores and both cost settings. The2026 partial-year diagnostic is not uniformly better. All period rows, including that diagnostic, are in {link('CANDIDATE_RESULTS.csv')}; CAGR in short-period audit rows is an annualized statistic, not a forecast.

At$50k, integer sizing was unstable and some severe-cost results reversed; I would not extrapolate this prototype to a small account. $250k was also tested and all results are retained. More capital does not establish future profitability. CI/YUMC also adds health-insurance and China-restaurant business risks and is not independent of equity-market risk.

**How the iterations changed the design**

1. **A: stand-alone variants.** Tested all17 lower-risk pairs and10 speculative pairs with daily/weekly/monthly rebalancing, bands, partial adjustment and SMA100/200 eligibility gates. Simple trend gates substantially reduced returns and did not consistently repair drawdowns. Keep them out of this candidate.
2. **B: strategy-level allocation.** Combined five preferred pairs with actual archived core paths and tested fixed blends, scheduled/banded transfers and discrete universal weights. Fractional strategy-unit results suggested a modest transfer benefit; they were only a diagnostic.
3. **C: native execution and whole micro contracts.** Replayed entries, exits, active resizing and cash flows. Integer constraints weakened the earlier results. The higher-growth stock pairs often lost too much return when repeatedly funding the slower core.
4. **D: preserve open trends.** Applied new core sizes only at future normal entries. This retains the trend bot's existing no-resizing behavior. It simplified the integration but did not magically remove all cost sensitivity.
5. **E: all17 lower-risk pairs and separate inner/outer rules.** Crossed daily/weekly stock rebalancing with static/weekly/monthly outer allocations. CI/YUMC ranked first by the weakest earlier/full-period CAGR increment across all three cores and both costs at100k. The weekly-stock neighbor also retained the mechanism. Repeated YUMC pairs are correlated evidence, not independent confirmations.
6. **F: collateral constraints.** Added20/30%-notional entry/transfer guards after an unguarded severe-cost case exposed a collateral shortfall. Kept the30% guard for the final prototype and its matched controls.
7. **G: Cover-style online allocation.** Used11 virtual fixed-weight experts with a uniform prior. Weights depend only on prior wealth, not the best hindsight expert. Its executable integer version did not improve the chosen fixed weekly controller. Do not add that complexity to the frozen prototype.

ALLE/KR was a useful competing integration: with the baseline core at100k it produced about10.47% CAGR and14.67% drawdown versus10.17% and16.66% for its static blend. However, its return advantage failed the severe-cost test and became negative when2020's incremental contribution was omitted. It is not the robust primary result. MEDP/PGR and ARES/COST improved some portfolio efficiency measures but substantially diluted the stock-heavy static blend's growth when capital was repeatedly transferred into core.

**Verification and strength of evidence**

The instrument-event replay reproduced six archived core paths to numerical precision, and the independent cash replay matched their funded P&L to less than$0.000001. Tests cover capital conservation, identical-return assets, causal prefixes and ledger behavior. See {link('verification.json')} and {link('core_event_parity.json')}.

All history here was already available and inspected. Pair selection itself comes from the earlier exhaustive search and uses current financial-quality/availability filters. Era resets and neighboring parameters help assess fragility; they do not erase selection bias or create a fresh out-of-sample test.

A conditional three-month-block bootstrap over the final204 candidate/cost/core combinations gives the primary baseline-stress candidate an annual log-growth increment interval of{pc(q.individual_lo)} to{pc(q.individual_hi)}; its simultaneous interval includes zero as well. One other cell, baseline-stress CNA/YUMC, crosses the final-family threshold narrowly, but this adjustment does not cover all preceding research choices and is not a license to replace the candidate with that statistical winner. Results are in {link('family_uncertainty.csv')}. Omitting each calendar year in turn leaves CI/YUMC's baseline-stress incremental log result positive; this is attribution, not a new backtest or forward confirmation.

The stock ledger uses adjusted daily closes, fractional shares and modelled proportional costs. Its dividends are effectively reinvested through those adjustments; this Phase3 engine does not reproduce the Phase2 observed stock BBO/size/30-day dividend receivable model. Futures use native15-minute execution and inherited roll conventions. Two archived stock-close timestamps, February28 and June30,2020, lack contemporaneous futures observations: the replay retains stale marks and suppresses outer adjustments there. Stops inside those data gaps cannot be verified. Intraday stock risk is absent from daily stock marks. These limitations prevent an execution-ready claim.

**What is now parked for forward evaluation**

The deliverable is a working, auditable capital controller that preserves open trends and adds a measured rebalancing effect to a specified blend. My recommendation is to freeze this research specification for genuinely unseen observation, while keeping the existing bot unchanged. I do not recommend deploying CI/YUMC as a superior replacement for the core, and I do not claim the higher-return alternatives passed the same robustness checks.

{link('controller_comparison.png','Wealth and drawdown comparison')} · {link('PROTOCOL.md','Complete iteration ledger')} · {link('round_g_comparisons.csv','Final complete comparison table')} · {link('ALL_PARENT_ROBUSTNESS.csv','All-parent ranking before collateral refinement')} · {link('RUN_COUNTS.json','Scenario counts')}
'''
(P/'REPORT.md').write_text(text)
# Neutral plot; same initial account and dates, no risk matching claims.
g=pd.read_parquet(P/'round_g_paths.parquet');u=pd.read_parquet(P/'unchanged_core_100k_paths.parquet');series={'Unchanged core':u['baseline_stress'],'Static core + CI/YUMC':g['baseline_stress_CI_YUMC__static_blend'],'Weekly capital controller':g['baseline_stress_CI_YUMC__weekly_entry']}
fig,ax=plt.subplots(2,1,figsize=(10,7),sharex=True)
for (name,s),style,color in zip(series.items(),[':', '--','-'],['.6','.35','black']):
 ax[0].plot(s.index,s*100000,label=name,linestyle=style,color=color);ax[1].plot(s.index,(s/s.cummax().clip(lower=1)-1)*100,linestyle=style,color=color)
ax[0].set_ylabel('Account value ($)');ax[1].set_ylabel('Drawdown (%)');ax[0].legend(frameon=False);ax[0].set_title('Same $100,000 start: added return also adds risk')
for a0 in ax:a0.spines[['top','right']].set_visible(False);a0.grid(alpha=.15)
fig.text(.08,.01,'2018–July 2026 • historical research • stressed costs • stock marks daily; futures native15-minute',fontsize=9);fig.tight_layout(rect=[0,.035,1,1]);fig.savefig(P/'controller_comparison.png',dpi=150);plt.close(fig)
manifest={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in P.glob('*.py')};manifest['production_config_sha256']=hashlib.sha256((P.parent/'trend_bot/config.yaml').read_bytes()).hexdigest();(P/'MANIFEST.json').write_text(json.dumps(manifest,indent=2));print('report words',len(text.split()),'scenarios',sum(counts.values()))
