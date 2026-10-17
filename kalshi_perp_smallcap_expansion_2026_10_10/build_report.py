from engine import *

R={n:json.load(open(P/(n+'_results.json'))) for n in ['hour_early','hour_later','minute','lead','pair','portfolio','stress']}
audit=json.load(open(P/'audit_results.json'));diag=json.load(open(P/'stress_diagnostics.json'))
def fmt(r):return f"{r['trades']:4d} {r.get('wins',0):4d} {r['net']:+9.4f} {r['drawdown']:9.4f} {r.get('without_best3',0):+9.4f}"
def row(label,r):return f'{label:49s} '+fmt(r)
inventory=[]
for t,m in M.items():
 raw=json.load(gzip.open(D/(t+'_1h.json.gz'),'rt'))['candlesticks'];q,f=load(t,3600)
 inventory.append({'ticker':t,'class':m['asset_class'],'status':m['status'],'contract_size':m['contract_size'],'raw_hour_candles':len(raw),'valid_hour_candles':len(q),'first_utc':dt.datetime.fromtimestamp(min(q),UTC).isoformat() if q else None,'last_utc':dt.datetime.fromtimestamp(max(q),UTC).isoformat() if q else None,'fee_taker':FEES.get(t),'funding_prints':len(f)})
save('inventory_coverage.json',inventory)
lines=['ALL COMPARISONS — each scenario is a separate replay, NOT independent evidence.',
       'Trades Wins   Net($)    DD($)    Net excluding best three trades($)',
       'Early/later each restart at their stated capital; do not sum as continuous PnL.']
for name,data in R.items():
 lines+=['','===== '+name+' =====']
 for k,r in data.items():lines.append(k+' '+fmt(r))
(P/'APPENDIX.txt').write_text('\n'.join(lines)+'\n')

leadrows=[]
for title,t,rule in [('DOGE trend','KXDOGEPERP','h_ema8_32'),('DOGE breakout','KXDOGEPERP','h_channel48'),('LTC breakout','KXLTCPERP','h_channel48')]:
 leadrows.append(row(title,R['lead'][f'{t}/{rule}/full/L2/slip1/delay60']))
for title,t,rule in [('Silver reversal (pre-entry revalidation)','KXSILVERPERP','h_reversal24'),('Palladium reversal (pre-entry revalidation)','KXPALLADIUMPERP','m_reversal240')]:
 leadrows.append(row(title,R['portfolio'][f'{t}/{rule}/capital100/full/L2/slip1/delay60/revalidate1']))
comb=[]
for lev in [2,3]:
 for slip,delay in [(1,60),(5,60),(1,120)]:
  comb.append(row(f'{lev}x margin leverage; {slip}bp/side; {delay}s execution',R['portfolio'][f'COMBINED100/full/L{lev}/slip{slip}/delay{delay}/revalidate1']))
splits=[]
for lab in ['early','later','full']:
 for slip in [1,5]:splits.append(row(f'{lab}, {slip}bp/side',R['portfolio'][f'COMBINED100/{lab}/L2/slip{slip}/delay60/revalidate1']))
stresses=[]
for lab in ['early','later','full']:
 for slip in [1,5]:stresses.append(row(f'intraminute adverse stop {lab}, {slip}bp/side',R['stress'][f'COMBINED100/{lab}/L2/slip{slip}/adverse_bars']))

report='''OTHER KALSHI PERPETUALS — $100 TOTAL CAPITAL
Research completed October 10, 2026. Research only; no activation or orders.

DECISION
The strongest new research candidate is a low-cost metals mean-reversion bot:
Silver using a 24-hour reference window, Palladium using a 4-hour window.
Allocate $50 to each, including idle cash. The defined minute-close-confirmed
version made $4.75 on ONE $100 bankroll, 46 entries, versus $2.99 with 5bp
slippage each side. Three-times position/margin leverage made $7.80/$4.82,
but 2x is the research default; higher leverage is not an additional edge.

This is positive exploratory evidence, NOT a verified live edge. The stricter
intraminute-stop version still made $3.50 overall but lost $0.24 in Oct1–9;
with worse execution it made $1.32 overall and lost $0.99 in that later period.
Therefore the candidate does NOT pass every robustness check. Recommend it
for the next frozen validation stage; do not claim this research authorizes
live deployment. DOGE trend is a separate higher-drawdown alternative with
positive results, but all its net profit depends on three large winners.

SCOPE / WHAT WAS ACTUALLY TESTED
Live catalogue: 28 perpetual tickers plus one inactive dated WTI future.
Screened all available hourly histories with four fixed rule families:
12/48-hour channel breakouts, EMA8/32 trend, and 24-hour mean reversion.
Minute tests: all eight non-crypto perpetuals plus SOL, NEAR, WLD, UNI selected
by current volume before their minute results. Five families: 60/240-minute
mean reversion, 60/240-minute breakout and US opening-range breakout.
Additional minute execution tests of hourly DOGE/LTC/Silver leads, and
two-leg Gold/Silver and Platinum/Palladium relative-value strategies.
This is 112 hourly asset/rule combinations, 60 minute combinations and
two pairs, with multiple cost, period and sizing scenarios. These are NOT
174 independent experiments or proof of statistical significance.
Some new products lack enough history or cannot satisfy sizing/margin rules;
zero trades/no history is not interpreted as a losing strategy.

DATA / PERIODS
Official Kalshi public candlesticks and funding, current account fee receipt,
current public margin parameters. Minute Silver history Sep10–Oct9;
Palladium Sep23–Oct9; DOGE/LTC June9–Oct9. End bound Oct10 00:00 UTC.
Early/later split Sep1 UTC for crypto and Oct1 UTC for other assets.
Silver/Palladium first model entries Sep13/Sep25 respectively. Full combined
period starts Sep10; Palladium's $50 sits idle until its own data/signals.
Raw coverage by all tickers is in inventory_coverage.json.

STANDALONE RESULTS — EACH ROW USES ITS OWN $100, DO NOT ADD THEM
Full continuous minute-execution replay, 2x position/margin leverage.
Trades Wins   Net($)    Max observed DD($)   Net without best3($)
'''+ '\n'.join(leadrows)+'''

Silver and Palladium rows include the labelled post-screen entry revalidation.
DOGE/LTC rows preserve the original trend/breakout rules. DOGE EMA: early
+13.29, later +4.53; higher-cost full +17.44. LTC early -2.50, later +11.83:
its positive full result is not consistent between periods. Different history
lengths preclude ranking these dollar totals as comparable expected returns.

ONE $100 METALS PORTFOLIO — $50 SILVER + $50 PALLADIUM
Trades Wins Net DD Net without best3, as above:
'''+ '\n'.join(comb)+'''

2x early/later separate restarts versus full continuous run:
'''+ '\n'.join(splits)+'''
Early/later figures do not sum to full because open positions, sizing,
cooldowns and boundary restarts differ. At 2x the full close-confirmed model
has 24 wins/22 losses: the edge hypothesis is payoff/reversion, not 90% wins.
Average 46/30 calendar days (~1.5 entries/day) is descriptive, not a forecast.
Positive marked PnL in four of five calendar-week buckets, including partial
weeks. There is only about one month of Silver data and two weeks Palladium.

DESIGN / CAPITAL
For Silver, evaluate completed hourly quotes against PRIOR24 hourly log-price
mean/std; fade |z|>=2, with volatility features based on earlier data. For
Palladium, evaluate every5minutes against prior48 five-minute observations
(4hours). Freeze mean/std on entry; target return to mean; stop at adverse
z4 or price stop max(0.2%,2*reference log-price standard deviation). Exits in
the primary design are CONFIRMED ON COMPLETED MINUTE CLOSES, next-minute
execution. Palladium also exits after8hours or15:55ET, weekday entries only
09:30–15:00ET; Silver max72hours. Re-entry cooldown1hour/15minutes.
This z measures historical price displacement. It is NOT the binary bot's
terminal-price cushion or a calibrated probability of winning.

Each $50 partition: margin<=50% own equity, reserve>=10; planned loss including
estimated round-trip costs<=min($1,2% own equity), daily marked loss plus
new planned risk<=3. Total initial margin at most50, total reserve at least20,
planned new risk up to2 across both. 2x is exposure divided by posted margin:
approximately $100 aggregate notional, NOT $200, because half cash is idle.
3x gives approximately150 notional within the same100 capital. Integer lots.
Partitions grow/shrink independently; no deposits, external hedge or transfer.
Stops/daily controls are entry gates and triggers, not guaranteed loss caps.

ENTRY REVALIDATION IS AN EXPLICIT NEW VARIANT
Original combined close-confirmed run:49 entries, +3.76, higher-cost +1.89.
Audit found that the baseline could enter after the quote was already beyond
its existing z4 stop or target. New pre-submission check rejects that stale
signal using the current executable side and FROZEN signal mean/std. No
future outcome filtering. Replaying the entire altered path (including
cooldowns/possible replacement entries), not deleting losing ledger rows,
gives46/+4.75 or +2.99 at higher cost. Preserve original in engine_initial.py.
This operational variant was conceived AFTER inspecting results. It must not
be portrayed as an original untouched holdout or an isolated dollar-saving
attribution to just the removed trades.

ADDITIONAL STOP / COST / UNCERTAINTY CHECKS
Use each subsequent minute's adverse bid-low/ask-high to trigger stop,
without granting any favorable intraminute profit exits; execute next quote.
This tests immediate/adverse-stop sensitivity rather than the specified
minute-close-confirmed rule. It materially changes which trades occur:
'''+ '\n'.join(stresses)+'''
At3x this stricter version makes+5.43 overall (+1.84 higher cost), but later
period -0.12 (-1.16 higher cost). Thus leverage doesn't fix the weak later
period. It is misleading to omit this result and call the primary model
robust to stop implementation.
Additionally rounding each taker-side fee UP to a whole cent on the same
2x stricter-stop trades leaves+2.76 baseline or+0.58 at higher costs.
This is a cost stress, not a claim about actual exchange fee rounding.
Three-day block resampling of full stricter-stop marked daily returns gives
an exploratory95% PnL interval -2.04 to+9.07 (2x). It crosses zero and is NOT
adjusted for the many strategies examined. No annualized profit projection.

OTHER MARKETS / REJECTIONS
Gold minute4hour reversal: early+1.31/later+1.26, but at5bp early-1.01/later
-0.08. The cost edge does not survive. Gold/Silver ratio pair full24 entries
-0.50 (5bp -2.37); Platinum/Palladium pair9 entries -2.04 (5bp -2.73).
Copper/Aluminum/US500/USTECH100 are too new/sparse here for both-period
validation; a few positive trades do not validate them. Platinum hourly
reversal showed+5.35 on four later trades and none early: insufficient.
Several altcoin trend results depended on the earlier rally: examples
XRP48hour breakout early+28.55/later-8.74, SOL+15.58/-2.11, BCH+22.52/-1.89.
These are hourly-screen separate restarts, not accepted minute-tested returns.
BTC/ETH tested families remained unconvincing after fees. No extrapolation
to every possible BTC/ETH strategy. All comparisons retained in APPENDIX.txt.

EXECUTION AND DATA LIMITS THAT MATTER
Taker fees applied on full per-contract notional BOTH sides: current account
rates12bp crypto,0.72bp metals/indices; historical bid/ask spread is explicit,
plus1bp per side baseline or5bp stress. Exact funding prints entry<t<=exit;
no collateral-interest assumption. Current fees applied retrospectively,
not verified historical fee-tier changes. Price/mark already per contract;
do NOT multiply by contract_size a second time.
Current initial multiplier1.3 and inverse side-specific leverage estimates
used for maintenance proxy, with0.85 margin-buffer trigger. Historical true
liquidation marks/risk configurations unavailable. No simulated buffer exits
establish absence of real liquidation risk during unobserved intervals.
Reject crossed/nonpositive/placeholder or>0.5% spread quotes; no forward fill.
Missing held-position marks in primary joint run1297minutes; largest blocks
120min during Thursday maintenance. DD is only observed valid-minute equity,
not continuous drawdown through gaps. Adverse-bar stress still has907 such
minutes plus50 rejected malformed extreme bars across the two raw histories.
Historical candle closes do not prove requested-size fills. Five of46 selected
entries have zero reported execution-minute trades; ten request more units
than that minute's reported volume. This is not proof that resting liquidity
was absent, but full book replay is unavailable. Current Saturday snapshot:
Silver spread~1.98bp, top ask8contracts; Palladium~254bp and sparse book, thus
Palladium correctly fails the10bp entry gate now. Saturday snapshot cannot
validate weekday liquidity. Fee/spread snapshots are not guaranteed fills.

SELECTION / WHAT IS STILL REQUIRED
Initial predeclared early-hourly screen required positive stress profit and
profit excluding best3: NONE passed. We explicitly then examined positive
two-period leads and selected the metals after seeing outcomes. The combined
close-confirmed model meets the numerical screen, but selection and the new
entry check happened retrospectively. Stricter stops fail later-period test.
Keep exact2x close-confirmed spec frozen for genuinely NEW observations.
Before live: verify qualifying-size bid/ask depth, actual fees/order fills,
minute-close versus emergency-stop semantics, margin/gap handling, partial
fills/restarts/reconciliation; collect fresh independent results. No new
observer or scheduled collection has been started by this research task.
Prefer this candidate to more BTC/ETH scalping parameter searches; retain
DOGE as a higher-risk comparator, not the default $100 deployment.

AUDIT / REPRODUCTION
'''+json.dumps(audit['counts'],indent=2)+'''
Zero audit errors. Counts include repeated ledgers across overlapping cost,
sizing and period scenarios, NOT independent trades. Decimal raw funding,
gross/net fees, bankroll recursion, integer size, chronology, margin/reserve
and daily risk checks; old-default parity and truncated-future indicator
checks passed. No production or stable-baseline files were edited.

Reproduce from this directory with Python/numpy/pandas/requests:
 python3 engine.py hour_early
 python3 engine.py hour_later
 python3 engine.py minute
 python3 validate_leads.py
 python3 pairs.py
 python3 portfolio.py
 python3 stress.py
 python3 audit.py
 python3 build_report.py
Raw data retained; rerunning fetch.py would replace current catalogue/risk
snapshots, so preserve this archive when reproducing. No credentials stored.

PRIMARY SOURCES
https://external-api.kalshi.com/trade-api/v2/margin/markets
https://external-api.kalshi.com/trade-api/v2/margin/risk_parameters
https://docs.kalshi.com/margin
https://help.kalshi.com/en/articles/16071417-perps-fees-explained
https://help.kalshi.com/en/articles/13823807-what-are-trading-hours
'''
(P/'REPORT.txt').write_text(report)
save('candidate_spec.json',{'status':'RESEARCH_ONLY_NOT_LIVE_VALIDATED','total_starting_capital':100,'default_position_margin_leverage':2,'partitions':[{'ticker':'KXSILVERPERP','capital':50,'rule':'h_reversal24'},{'ticker':'KXPALLADIUMPERP','capital':50,'rule':'m_reversal240'}],'reserve_per_partition':10,'margin_fraction_max_per_partition':.5,'planned_risk_per_partition':1,'daily_entry_risk_gate_per_partition':3,'revalidate_existing_exit_before_entry':True,'stop_semantics':'Completed minute closes; emergency/intraminute exits materially change results and require separate verification.','selection':'Retrospective; not an untouched holdout.','activation_authorized':False,'automated_collection_started':False})
print('Wrote REPORT.txt, APPENDIX.txt, inventory_coverage.json, candidate_spec.json')
