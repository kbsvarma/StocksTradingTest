# Safe_Y / Ideal_Y revisit — frozen research plan

Created 2026-10-03 before this study's results. Research only; no production
configuration, source, checkpoint, account or order changes.

Rebuild the old system's intent: buy the favorite around 55–65 cents, arm Safe_Y
after an advance, raise protection at Ideal_Y, sell only on reversal or hard
stop, otherwise hold to settlement. One entry per market; no re-entry.

## Data and separation

BTC primary, ETH separate. Raw historical market candles plus causal completed
Coinbase bars: Aug 13–Oct 2, 2026. Develop Aug 13–27, select Aug 28–Sep 11;
freeze selected configurations before reading their Sep 12–Oct 1, Oct 2 and
Oct 3 results. These dates have been used in other research and are NOT an
untouched holdout. Freeze at most two active-exit candidates per asset.
Retain negative candidates and the complete configuration inventory.

Historical books Aug 13–Sep 11 and captured Oct 3 WebSocket books provide a
separate execution check. They are displayed-liquidity simulations, not fills.
Only complete captures; sequence gaps invalidate a book until a new snapshot.

## Entry families

Bands 55–65 (primary), 60–70 (old reference), 50–70 cents (coverage control).
Time remaining: 2–14, 9–14, 5–8 or 2–4 minutes. Spread <=2 cents. Favorite from
the completed quote midpoint. Filters: none; cushion z>=0.25; z>=0 plus latest
underlying return >=0.25 prior 60-return sigma in favored direction; regularized
probability estimate minus ask >=3 or 6 cents. Probability fit uses only the
development period and completed prices. Report coverage lost to every filter.
The band is a genuine buy limit: failed pre-submission next quotes do not consume
the whole market, so a later qualifying signal can be used. Once entered, no
second position. Band availability is not assumed to be 100%.

## Exit families, defined before outcomes

Controls: hold to settlement; absolute 45-cent stop only.
Old reference: absolute 45-cent stop, Safe +6 cents, Ideal +13, zero buffer.
Protected variants: stops at entry minus 10/15/20 cents, Safe +6/+10 cents,
Ideal +13/+20 cents, reversal buffer 0/3/6 cents; all combinations.
Time-scaled variant: stop distance .15, Safe .06, Ideal .13, buffer .03,
all multiplied by clamp(sqrt(minutes_remaining/7), .7, 1.4) at entry.
Trailing variant: same .15/.06/.13/.03 levels, after Ideal ratchet protection
to peak bid minus .06. Arming never immediately sells; protective tiers only
rise, never fall. Hard stop remains in force throughout.

## Execution and accounting

Primary long-history model observes completed-minute bid/ask; buys at the next
minute opening ask, sells at the next opening bid after an exit trigger. It is
a quote proxy, not depth evidence. No use of candle highs to claim a target was
fillable. Sale fills at the observed bid, never a promised stop/target price.
Pending exits retry at the next observable execution quote; final fallback is
settlement. No sale fee on settlement; fee on BOTH sides of an early exit.
Buy and sale rounding follow signed cash-flow alignment to $0.0001; also check
cent rounding. Standard extra friction .25 cent/contract/transaction, stress
1 and 2 cents per transaction; secondary full-minute latency.

Report one-contract dollars, not five-contract dollars mislabeled as one. Five
contracts must recompute fees, liquidity and cash/risk if investigated. Report
net-positive vs net-negative trades separately from eventual settlement wins.
Report max realized drawdown, daily block bootstrap, monthly split, window
coverage, exit reasons and hold-to-settlement control on IDENTICAL entries.
Initial separate research cash $100, daily loss budget $10 including full cost
at risk (a stop is not guaranteed), reserve full entry debit, one position per
asset/window. Portfolio constraints are applied to frozen candidates, not used
to pick a winning historical permutation.

## Selection and decision

Select separately per asset: best validation net among active exits with
positive development and validation net after .25-cent friction, >=200 trades
per block and >=35% window coverage in each; rank net/number of listed windows.
Second candidate allows >=15% coverage to investigate a selective entry edge.
If no candidate passes, retain the highest validation-net failure in each
group as an explicitly failed diagnostic. Also keep both old band references.
Compare neighborhood variants, later stress, gap/latency sensitivity and recent
depth before recommending paper observation or live implementation. No claim
of a proved edge merely because a large searched grid has one positive cell.

The new pure state machine and audited trade ledgers are the reusable output.
Saved stable checkpoint and active z2/z3 production source must match before
handover. Mirror the study to the new research server and verify hashes.
