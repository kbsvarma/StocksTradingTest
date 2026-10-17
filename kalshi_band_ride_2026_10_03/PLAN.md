# Buy 55–65 cents and ride: continuation versus reversal

Frozen before this study's output, 2026-10-03. Public-data/offline research only.
No live or checkpoint edits. BTC primary, ETH separate. One contract, one entry
per market, hold to settlement: no Safe_Y, Ideal_Y or early stop in this test.

Question: conditional on a favored side's ask at55–65 cents, how often does it
remain the final winner? Do time, signed cushion z, momentum, quote direction or
the already-frozen probability estimate isolate an advantage? Also price the
opposite side at its actual ask, not one minus the favorite ask.

Three definitions, never conflated:
- Final reversal: the favored side at entry loses official settlement.
- Temporary reversal: a later observed midpoint falls below50% for that side.
  Completed-minute sampling misses intraminute reversals; tick books separately
  measure them on the covered Oct3 subset. A temporary reversal may recover.
- Ten-cent drawdown: observed bid falls >=10 cents below entry ask.

Raw data: existing causal arrays for Aug13–Oct2 and existing Oct3 subset. Fit/
development Aug13–27, selection Aug28–Sep11, later Sep12–Oct1, Oct2 diagnostic;
Oct3 short execution check. All periods were used in other research; this is
exploratory, not an untouched holdout. No new model fitting.

Primary baseline: first executable band opportunity with2–14 minutes remaining.
Time partitions:12–14,9–11,6–8,3–5,1–2 minutes. Within any cell take the first
qualifying observation per market, avoiding repeated-minute pseudo-replication.
Cells/strategies overlap across tables and must not be summed as unique trades.

z diagnostic bins: below0,0–.25,.25–.5,.5–.75,.75–1,1–1.5,1.5–2,>=2. The z is
the signed pre-open-reference/60-return-sigma score available at entry, not a
recomputed future z or calibrated settlement probability.

Small fixed strategy screen: both directions, six time windows and filters:
none; z>=0,.25,.5,.75,1,1.5,2; z<0; z<=-.25; favored momentum>=.25 sigma with
z>=0; quote midpoint advancing or retreating>=2c since previous completed minute;
frozen estimated favored-side probability minus ask>=3c or6c; z>=.5 plus positive
momentum. Keep all configurations and failed cells.

Execution: first next-opening quote still showing the original favored side
at55–65c, spread<=2c. Buy that side's ask or the opposite side's ask. Recheck later
opportunities after a failed quote; never use an entry outside the stated band
for the favorite. Hold actual settlement. Fees charged only at entry, .0001
cash precision, plus .25c standard /1c and2c extra friction. Test 1-minute quote
delay and actual archived depth for selected candidates. Same-side and opposite
direction are alternatives, never a combined betting instruction.

Select one rule per asset/direction using only early blocks: minimum100 trades
per block, >=10% archived-window coverage per block, both positive at1c extra
cost. Rank validation net per archived window. If none passes, retain highest
validation result meeting sample/coverage as a failed diagnostic. Freeze before
examining its later results. Include first-band baseline regardless of return.

Report sample size, mean entry cost, fee-aware break-even win rate, final win/
reversal rates, transient reversals and recovery, net P&L, coverage, drawdown and
day-block uncertainty. Frequency alone and a reversal rate above/below50% do
not establish an edge. Preserve current production and archive this study on
the new server with hashes.
