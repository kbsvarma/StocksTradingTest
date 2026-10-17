# BTC / ETH follow-up — frozen research scope

Created October 2, 2026 ET, before this follow-up's candidate results. Production and the September 28 stable checkpoint remain unchanged.

## Questions

1. Does BTC/ETH liquidity translate into executable five-contract depth, rather than merely reported volume?
2. Can a BTC/ETH-only additional lane improve the combined, currently live z2 + z3 design under its actual 20-contract / $19 / $10 shared limits?
3. Do causal volatility, momentum, market-price and official settlement-index inputs improve probability estimates enough to pay the spread and fees?

## Data and evaluation

- Fit probability models on August 13–27 UTC. Examine August 28–September 11 separately from September 12–October 1. These 50 days have been used for earlier strategy research: later-period results are retrospective validation, not an untouched holdout.
- Separately fetch October 2 UTC as a fresh one-day check. It is too short to establish an edge.
- Features use completed minutes only. Official outcome and final volume may be used for labels/descriptive liquidity analysis, never predictive features. Each market has total training weight one, despite repeated minute observations.
- Baseline: original z2 plus premium z3, each five contracts, all four live assets. New candidates are BTC/ETH only, tested jointly and individually as exploratory comparisons.
- Screen the 14 named candidates below, without tuning thresholds after results. Asset selection multiplies the family to 42 comparisons. Model diagnostics are reported separately from trading results.
- Deduplicate markets, simulate settlement release, cash, same-asset exclusion and shared exposure/daily limits. Report displaced baseline trades and incremental combined P&L, not just attractive standalone P&L.
- Charge taker fees plus 1 cent per contract execution stress; also evaluate 2 cents and worst permitted limit price. Use subsequent recorded order books where available (August 13–September 11), require full displayed size five, and repeat with 10-second latency. Later candles are quote proxies without order-book depth or queue/fill proof. No claim that a candle backtest proves executable profit.
- Report trade counts, wins/losses, net, drawdown, daily concentration, per-asset results and day-block bootstrap intervals. Bootstrap does not protect against regime changes or unobserved intraday loss paths.

## Frozen candidates

All require spread <= 2 cents, use the existing conservative tick rounding and at most one entry per market. Five contracts. Existing live lanes have priority at equal timestamps.

| Candidate | Conditions |
| --- | --- |
| cheaper_z2 | z >= 2; ask 65–<85c; 3–10 minutes; cap 93.9c |
| early_z2 | z >= 2; ask 70–<95c; 11–13 minutes; cap 93.9c |
| late_z2 | z >= 2; ask 85–<97.5c; 1–2 minutes; cap 97.5c |
| late_z3 | z >= 3; ask 90–<98.5c; 1–2 minutes; cap 98c |
| gaussian_edge8 | z >= 1.5; Gaussian score minus ask/fee >= 8c; ask 55–<95c; 3–10 minutes |
| student_edge8 | Same, variance-normalized Student t with 3 degrees of freedom |
| adaptive_edge8 | Same, Gaussian score using max(15-minute, 60-minute volatility), 8c threshold |
| adaptive_edge5 | Same adaptive score, 5c threshold |
| basic_edge3 | Regularized market-price/z/time/asset logistic probability; edge >= 3c; ask 55–<95c; 1–13 minutes |
| basic_edge5 | Same, 5c threshold |
| rich_edge3 | Basic model plus causal volatility ratio, 3/5-minute spot momentum, cross-asset momentum, prior-minute quote change and prior-minute volume; 3c threshold |
| rich_edge5 | Same, 5c threshold |
| official_z2 | Completed CF index last-second price and published strike, 60-minute volatility, conservative original remaining-time variance; original z2 price/window |
| official_average_edge5 | Completed CF index, published strike; Brownian variance for the final 60-second average: sigma^2 * (minutes_remaining - 2/3); edge >= 5c; z >= 1.5; ask 55–<95c; 1–13 minutes |

The last two initially have only August 13–September 11 coverage. Their fit-period results cannot be presented as validation. The average-variance formula is a model assumption, not a calibrated probability or prediction guarantee.

## Decision

Prefer a candidate whose incremental benefit survives both validation periods, books/latency and cost stress, with enough independent losing outcomes to assess tail risk. If none qualifies for live sizing, freeze the strongest coherent hypothesis for a separate no-order forward experiment and state the failure explicitly. Never relax the stable live rules simply to manufacture more activity.
