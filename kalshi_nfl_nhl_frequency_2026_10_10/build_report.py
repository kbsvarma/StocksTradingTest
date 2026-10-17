"""Build the research handover from audited result files; no network."""
import json
from pathlib import Path

P = Path(__file__).resolve().parent
def read(name):
    return json.loads((P/name).read_text())
r = read('results.json')
o = read('operational_results.json')
p = read('period_spread5_results.json')
j = read('joint_results.json')
e = read('evaluation.json')
def table(rows):
    lines = ['Design / sample | Trades | Wins | Losses | Net USD', '--- | ---: | ---: | ---: | ---:']
    for name, v in rows:
        lines.append(f"{name} | {v['trades']} | {v['wins']} | {v['losses']} | {v['net']:+.2f}")
    return '\n'.join(lines)

recent = table([
    ('NFL old pregame-favorite filter, matched recent sample', r['KXNFLGAME/f60/m5/p90/base/recent']),
    ('NFL any leader, final 5 min, 90–<95c, 2c spread', r['KXNFLGAME/leader/m5/p90/base/recent']),
    ('NFL same, 5c spread sensitivity', o['spread5/KXNFLGAME/leader/m5/p90/base/recent']),
    ('NFL 90–<98c, 2c spread comparator', r['KXNFLGAME/leader/m5/wide/base/recent']),
    ('NHL full-game any leader, final 5 min, 90–<95c', r['KXNHLGAME/leader/m5/p90/base/recent']),
    ('NHL period leader, final 5 min, 90–<98c, 5c spread', p['wide90/lead1/m5/base/ALL/REG']),
])
nfl = table([(label, o['spread5/KXNFLGAME/leader/m5/p90/base/'+part]) for label,part in [
    ('Sep–Nov 2025 training interval','previous_early'),
    ('Dec 2025–Feb 2026 validation interval','previous_late'),
    ('Sep 9–Oct 8, 2026 recent regular season','recent'),
    ('All tested regular/postseason intervals','all_regular')]])
stress = table([(label, o['spread5/KXNFLGAME/leader/m5/p90/'+sc+'/all_regular']) for label,sc in [
    ('NFL baseline: fees + 1c/contract friction','base'),('NFL fees + 2c/contract friction','cost2c'),
    ('NFL execution delayed to next minute close','delay60'),('NFL 90-second source lag','source90'),
    ('NFL both delays','both_delays')]])
nhl_stress = table([(sc,p['wide90/lead1/m5/'+sc+'/ALL/REG']) for sc in ['base','cost2c','delay60','source90','both_delays']])
joint = table([(label,j[key]) for label,key in [
    ('One $100, recent NFL + NHL periods','base/recent'),
    ('One $100, October entries only','base/october'),
    ('October, 2c friction','cost2c/october'),
    ('October, execution delay','delay60/october'),
    ('October, 90-second source lag','source90/october'),
    ('October, both delays','both_delays/october')]])

report = f'''NFL AND NHL: MORE REGULAR LATE-FAVORITE TRADES
Research snapshot: October 10, 2026 ET. RESEARCH ONLY; no live changes.

DECISION
Prioritize the NFL current-leader rule: regular/postseason, final five minutes
of Q4, signal ask 90–<95c, five contracts, hold settlement. Removing the
pregame-favorite restriction is the useful change. Keep the five-minute gate.
Allowing up to a 5c bid/ask spread is a secondary operational candidate, only
with actual five-contract buy depth verified before an order. The original
training-selected rule used 2c; the 3c/5c tests were added after base results.

For NHL, reject the more frequent full-game leader rule: it loses in older
data. The separate first/second/third-period winner markets are a new, more
frequent research candidate. Their short record warrants prospective testing,
not the same confidence as the NFL rule. This report freezes both candidates
without enabling them. Current crypto/commodity lanes and stable baseline
are untouched.

All numbers below use five contracts and a standalone $100 starting bankroll,
reported taker fees plus 1c per contract execution friction unless specified.
These are simulated quote-based entries, not executed trades or forecasts.

RECENT FREQUENCY AND PROFIT
NFL: all 65 completed regular-season games from Sep 9–Oct 8, 30 calendar days.
NHL: 72 completed regular-season games from Sep 29–Oct 10, 12 calendar days,
through the frozen snapshot; Oct 10 is incomplete.

{recent}

Matched old/new NFL comparison: removing the pregame filter adds 12 games
(10 to 22), with all ten shared entries unchanged. The wider spread adds two
more entries (24 total) and changes some entry timing. The old prior-report
count of seven used a different date/source universe; it is not a clean
seven-to-24 one-variable comparison.

NFL spread-5 entries by active Tuesday-start week: 5, 4, 8, 7. This means
roughly 4–8 per active NFL week in this sample, not daily trading. A subsequent
partial week had no entry. October had SEVEN NFL entries, net +$2.3171.
NHL period entries: 18 across 16 games and nine active entry days; 15 entries
in October, net +$2.8446. Full coverage averages 1.5 entries/calendar day,
with several zero-entry days. Three periods do not guarantee three trades.

NFL: OLDER-SEASON CHECK
Historical scheduled-game window Sep 1, 2025–Feb 15, 2026. Training before
Dec 1; validation thereafter. Regular season and postseason only.

{nfl}

The training-selected, tighter 2c-spread version produced 55 trades/+13.0707
in training, 29/+4.8280 in validation, and 22/+7.5578 recently. Its combined
106 entries were 104W/2L, +25.4565. Selection required at least 25 training
entries and positive net in all five training scenarios, ranked by the worst
training stress result. NHL's selected rule and its failure are retained.

{stress}

The 5c version remains positive in each individual validation stress, too.
But the spread allowance was examined after base outcomes; it is not a new
untouched holdout. Pooled NFL 113/115 gives a descriptive 95% Wilson interval
of 93.88–99.52%, versus mean modeled break-even 93.38%. That interval does
not adjust for strategy selection, feed revisions, dependence, or fill error.

NFL settled-P&L drawdown: $4.7698. Observed bid-mark drawdown: $13.7030, with
402 stale/missing held marks and maximum observed quote age 1,599 seconds.
This is sampled mark risk, not an executable continuous drawdown bound.
One recorded loss: GB led Chicago 16–6 with 4:31 left on Dec 20, 2025;
the 94c entry ultimately lost $4.7698 after costs. Late leaders still lose.

WHAT DOES NOT HELP
At the same 2c spread, opening the NFL price band to 90–<98c increases recent
entries 22 to 31 but adds only $0.4787. Across both season windows it increases
106 to 149 entries while REDUCING net $25.4565 to $21.3110. Its combined-delay
training result is negative. More expensive entries are not the best tradeoff.
Entering with ten minutes left gives 43 older validation entries, 38W/5L,
-$11.1024; fifteen minutes gives 48, 43W/5L, -$8.9160. Retain five minutes.
Preseason also fails: the core five-minute NFL rule gives 15, 12W/3L,
-$10.2301. Do not pool preseason with regular-season evidence.

NHL FULL-GAME REJECTION
Any leader/final five minutes/90–<95c looks good recently (26, 25W/1L,
+$4.6555), but Jan–Mar 2026 gives 203, 183W/20L, -$24.7995. Pooled net is
-$20.1440. The training-selected pregame>=55%/90–<98c rule gives +$3.9903
in training, then 44 validation entries/40W/4L/-$5.7270. Its full pooled
+$1.9296 turns -$2.9704 with a second cent of friction. Reject it.

NHL PERIOD-WINNER CANDIDATE
Markets: KXNHL1P, KXNHL2P, KXNHL3P. Use the leader of THAT PERIOD, calculated
by subtracting the score known at period start from cumulative goals. At
least a one-goal period lead, final five minutes, signal ask 90–<98c, up to
5c spread. YES only: there is a third TIE outcome, so the other team's NO
is not equivalent. One entry per physical period; all periods share $100.
216 matched game-periods; every period result reconciles to its official
settlement. First period: 8W/+1.6547; second: 4W/+.7336; third: 6W/+1.2411.

{nhl_stress}

Spread <=2c gave eight entries/+1.5617; <=3c gave ten/+1.9283; <=5c gives
18/+3.6294. These spread tests were posthoc. Historical endpoint returned
no older period markets; available catalogue begins Sep 29. Eighteen wins
are NOT a validated 100% win rate: Wilson lower bound 82.41%, versus 95.97%
mean modeled break-even. One roughly $4.8 loss erases the observed profit.
Settled drawdown is zero only because this small sample has no losses;
period intratrade drawdown was not estimated. Source/execution delays cut
frequency sharply, even though the resulting small samples remain positive.

JOINT $100 PORTFOLIO: COMPUTED CHRONOLOGICALLY, NOT SUMMED ACCOUNTS
{joint}

The recent joint run retained all 42 individual entries, peak reserved debit
$18.7027 and minimum free cash $85.1466. October retained 22, peak reserved
$9.6334. No capacity/risk skips in these samples. This is exploratory pooling
of the two research candidates, not proof that the NHL contribution will
persist, and not a shared-budget replay with existing live crypto lanes.

DATA AND EXECUTION METHOD
1,084 archived full-game event tickers: 398 NFL and 686 NHL, including
preseason, an NHL postponed/refunded game, three Dec 31 boundary games, and
rescheduled duplicates. Window/season filtering happens before portfolio
replay; physical-game IDs prevent duplicate bets across rescheduled tickers.
1,083 have usable timestamped play states. Current-window coverage is complete
for the collected 65 NFL/72 NHL games. All earlier returned records and
coverage gaps are retained; missing data are not labeled losing trades.

168 full-game profiles: price bands 85–<90/90–<95/95–<98/90–<98c; remaining
time 5/10/15 minutes; current leader, pregame >50/>=55/>=60%, multi-score
leads, and NFL explicit post-play possession. Five scenarios and seven
overlapping periods. APPENDIX.txt discloses every result, including failures.
Period-market expansion and spread sensitivity are explicitly later tests.
NHL goalie/manpower filters were not reconstructed without causal source data.

Decision uses completed one-minute quotes and timestamped ESPN post-play
scores, with 30s imposed source lag and 120s maximum age, then rechecks the
leader at execution. No overtime entries. NFL ties/scalar refunds preserved.
Cheapest equivalent YES/NO contract is selected using decision quotes BEFORE
examining future execution; NHL periods use YES only. Require prior 10-minute
executed volume >=5. This is an activity filter, not verified order-book depth.
Baseline entry uses next minute's opening ask at a modeled decision+3s,
max(signal ask, execution ask), with no favorable improvement; limit is
min(signal ask+1c,98c). The signal price ceiling is NOT an absolute fill ceiling.
Hold until actual settlement timestamp, releasing cash only then.

Costs: ceil-to-$0.0001 taker fee 0.07*series_multiplier*5*p*(1-p), plus 1c per
contract. Period multipliers read from each official series. Historical NFL
pre-Jan-2026 multiplier is assumed 1 from the available schedule; those fees
are normalized estimates, not account fill receipts. No maker-fill assumption.
Portfolio: five contracts/entry, max five positions/25 contracts/$25 reserved
debit, $20 daily realized-loss-plus-open-risk gate, $2 free cash floor.

Historical ESPN event times do not prove first receipt or revision time.
Some modified timestamps are much later. Imposed lags and causal code checks
cannot remove that source limitation. Minute asks do not prove five contracts
were executable three seconds later. Fees/slippage/depth and real-time score
arrival must be measured at actual qualifying signals before live acceptance.
No live-observer daemon was installed. The bounded two-snapshot check saw an
NHL late leader with a $1 ask and no eligible fill, so proves no deployment
readiness. Research selection reused recent data; no prospective holdout claim.

ARCHIVE REPAIRS AND AUDIT
Flexed NFL dates reconciled to exact teams/ESPN game IDs. GB–DAL Sep 28, 2025
had a stored close before kickoff; official executed same-market trades proved
it remained open. Original metadata retained; conservative effective close
bounded by the latest verified trade. Its scalar tie remains in the universe.
One zero-volume rescheduled market's query was bounded to game hours. Historical
decimal-dollar quote strings normalized separately from integer cent fields.
Postponed LA–CBJ's 48/52 refund is retained, not falsely audited as a played tie.
Dec 31 NHL records remain archived but are excluded from the Jan–Mar portfolio.

Both accounting/chronology/ownership/risk audits pass; nine causal tests pass.
Audit counts refer to repeated scenarios, NOT independent observations.
Raw public response receipts, repairs, scripts and full ledgers reside at:
/home/varma/kalshi_nfl_nhl_frequency_2026_10_10 on 192.168.3.136.
Local directory carries results/scripts/report and a checksum manifest.
Reproduction order on server: study.py; rank_candidates.py; audit.py;
operational.py; periods.py [default, .03, .05]; evaluate.py; joint.py;
audit_extra.py; python -m unittest -v test_study; build_report.py.
Collection uses public GETs only; offline replay needs no credentials.

Sources: official Kalshi markets, rules, candles, trades, fee metadata and
settlements, plus ESPN schedules and timestamped play records.
https://docs.kalshi.com/api-reference/historical/get-historical-markets
https://docs.kalshi.com/api-reference/live-data/get-game-stats
https://kalshi.com/docs/kalshi-fee-schedule.pdf
https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary
https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/summary
Exact request URLs/retrieval times are retained in raw source receipts.
'''
(P/'REPORT.txt').write_text(report)

lines = ['ALL RETAINED COMPARISONS — overlapping scenarios, not independent samples.',
         'Fees +1c baseline; cost2c=+2c; delay60=next close; source90=90s lag.',
         'Full scenario key | trades | wins | losses | scalar | net | settled drawdown']
for filename in ['results.json','operational_results.json','period_results.json',
                 'period_spread3_results.json','period_spread5_results.json','joint_results.json']:
    lines.append('\n'+filename)
    for key,v in sorted(read(filename).items()):
        lines.append(f"{key} | {v['trades']} | {v['wins']} | {v['losses']} | {v['scalar']} | {v['net']:+.5f} | {v['drawdown']:.5f}")
(P/'APPENDIX.txt').write_text('\n'.join(lines)+'\n')

spec = {
    'status':'RESEARCH_ONLY', 'live_orders_enabled':False,
    'capital':100, 'contracts':5, 'max_positions':5, 'max_contracts':25,
    'max_reserved_debit':25, 'daily_realized_loss_plus_open_risk':20, 'cash_floor':2,
    'primary':'NFL leader, regular/postseason, Q4 final five minutes, signal ask [0.90,0.95)',
    'primary_training_spread_max':0.02, 'secondary_operational_spread_max':0.05,
    'nhl_experimental':'YES period leader, >=1 goal in that period, final five minutes, signal ask [0.90,0.98)',
    'nhl_series':['KXNHL1P','KXNHL2P','KXNHL3P'], 'nhl_spread_max':0.05,
    'nhl_no_equivalent_no_side':True, 'nhl_one_entry_per_period':True,
    'source_lag_seconds':30, 'source_max_age_seconds':120, 'prior_10min_volume_min':5,
    'limit':'min(signal ask + 0.01, 0.98); no favorable execution improvement',
    'exit':'official settlement', 'friction_per_contract':0.01,
    'required_before_live':['fresh qualifying five-contract depth and fee measurements',
                            'source first-receipt and revision validity',
                            'prospective outcome validation, especially NHL',
                            'restart/partial-fill/reconciliation controls and shared-live-budget review'],
    'selection_caveat':'Core training selected retrospectively; spread and NHL period studies posthoc; recent history reused.'}
(P/'research_spec.json').write_text(json.dumps(spec,indent=2)+'\n')
print('Wrote REPORT.txt, APPENDIX.txt, research_spec.json')
