# BTC every-window loss-control experiment

Research only. Preserve the live z2/z3 bot and September 28 checkpoint. User requests BTC first, an entry each 15-minute window, small stops and alternatives that save 10–15c from eventual losses, repeated investigation across variants.

## Question and accounting

Can a causal side-selection rule and loss-control exit create positive net returns at near-complete participation? One contract, one entry per listed BTC market; no averaging down, stake escalation, re-entry, or selective skipping of difficult forecasts. Missing model inputs fall back explicitly to the market favorite. Missing/unexecutable quotes are measured operational exclusions, not assumed fills. A missed initial entry is retried on subsequent completed minutes until the final minute.

Every exit is evaluated against holding the exact same entry through settlement. Report original settlement accuracy separately from realized positive-P&L trades. Split exit benefit into recovered cash on eventual losers and foregone payout on eventual winners, after sale fee and additional execution allowance. A 10c/15c salvage threshold does not guarantee 10c/15c recovery when the book gaps.

## Pass 1: fixed mechanical family, before inspecting results

Entry directions: favorite, underdog, always YES/NO controls, Coinbase strike-direction and its opposite, last-minute momentum, last-three-minute momentum and its opposite, existing frozen rich-model value side, official-index direction, empirical physical-model value side, and market-combined probability value side. Previously fitted models retain their earlier coefficients; no fitting on later returns.

Entry scheduling: fixed 14/12/10/8/6/4/2/1 minutes remaining; first selected-side ask in 55–65c with unconditional entry deadline at 8 or 4 minutes. Start observing after the first completed minute. No minimum z or predicted edge requirement. Entry signal and subsequent executable quote must be valid with spread <=5c; prices strictly between zero and one. Report coverage relative to all archived listed windows, and missing clock slots separately.

Exit families, all holding survivors to official settlement:

- Hold control.
- Immediate loss-distance stops at 3/5/8/10/15/20/25/30c below entry.
- Absolute salvage stops at executable bid 5/10/15/20/25/30/40/45/50c.
- Confirmation versions of distance 5/10/15/20c and salvage 10/15/20/30/40c: require two consecutive completed-minute breaches, or three for the selected 10/15c variants.
- Stops activated only with 8/5/3/2/1 minutes remaining, for distance 5/10/15/20c or absolute salvage 10/15/20/30c.
- Time-scaled loss distances 10/15/20c using sqrt(remaining / remaining_at_entry), capped at the original distance.
- Trailing loss protection after a 10/20c favorable bid gain, with 5/10/15c trailing distance. No fixed take-profit target.
- Underlying invalidation: signed Coinbase z <= −0.25/−0.5/−1, paired with a mark below entry; optional two-minute confirmation. This is a causal warning rule, not knowledge of the outcome.
- Probability exit diagnostics: sell when frozen rich/physical/blended current probability is below net executable sale proceeds by 0/2/5c, while the position is marked below entry. Model uncertainty remains a limitation.

Finite grid and all results are retained. Select family leaders only on Aug13–27 and Aug28–Sep11: require >=98% participation in both, positive after modeled fees +0.25c per transaction, rank by the smaller per-trade return of the two periods. Freeze leaders and controls before Sep12–Oct1 and Oct2 evaluation. Always-YES/NO controls may be displayed but do not become a recommended design on sample imbalance alone. If no candidate passes, retain the least-bad leaders honestly. A second pass may follow a diagnosed failure, with a new recorded hypothesis and no claim of fresh holdout status.

## Execution and verification

Primary screening uses completed-minute quote decisions and subsequent minute opening quotes as entry/exit proxies. No fills at nominal stop thresholds; sell at the observed subsequent bid. Triggered exits remain pending until an executable bid or settlement. No intrabar low/high oracle and no sale after close. Both purchase and sale fees use quadratic multiplier one and direct-member cash rounding. Base cost 0.25c per executed transaction; repeat 1c/2c cost and a full-minute delay. These candles cannot prove continuous-stop performance.

Recheck frozen finalists on archived approximately-30-second books and current dense full-depth captures. Require displayed quantity and chronological decision/arrival timestamps. Compare minute monitoring with continuous observed-book monitoring, because a tight stop may be hit and recover within a minute. Full-size fill prices, including adverse gaps, replace thresholds. Assess latency and cost stress, not just headline P&L. Current captures and all older history have been used in prior work: exploratory retrospective evidence, never untouched validation.

Report results at one contract, entry count/participation, original directional accuracy, cash win/loss rate, average cash gain/loss, net, drawdown, worst day, cost/delay stress, stopped eventual winners/losers, and recovery economics. Show the user's exact 10c/15c salvage policies even when they fail. Do not count repeated forecasts as independent bets. Use date-block uncertainty and chronological selection; multiple trials remain a substantial selection risk.

Outputs are isolated, tested, mirrored to the new server, and documented in research memory. A positive backtest is a research candidate, not automatic live activation or a promised profit.
