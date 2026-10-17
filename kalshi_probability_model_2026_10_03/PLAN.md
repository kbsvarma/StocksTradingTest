# BTC and ETH settlement probability — frozen research protocol

Research only. No order submission, activation, or changes to the live z2/z3 bot or stable checkpoint.

The earlier 55–65c ETH/pullback candidate was found in a retrospective 384-rule search. It is a hypothesis, not an established design or an independent edge. The archive has been repeatedly examined; chronological evaluation below is retrospective, not an untouched holdout.

## Mechanism and models

Use official CF Benchmarks minute-end values, the published market strike, and the standard deviation of the previous 60 contiguous completed minute returns. Forecast the arithmetic mean of the final 60 one-second index readings. The continuous Brownian approximation gives remaining variance sigma² × (minutes_remaining − 2/3). Use distance (S/K − 1) only for diagnostics; the probability threshold for normalized future returns is (K/S − 1)/(sigma sqrt(minutes_remaining − 2/3)). No drift or momentum parameters, time buckets, or outcome-selected z gates.

1. Gaussian diffusion: a diagnostic of the uncalibrated textbook assumption.
2. Empirical diffusion: on August 13–27 UTC only, collect normalized future returns (official expiration value / current CF price − 1) / horizon volatility. Symmetrize them to enforce zero drift. Each market has total weight one. The empirical cumulative distribution calibrates volatility scale and fat tails without selecting profitable time cells. Fit separately for BTC and ETH.
3. Primary model: a two-coefficient, zero-intercept logistic combination of market midpoint log odds and empirical diffusion log odds. Fit nonnegative coefficients by weighted log loss on August 28–September 11 only, with ridge penalty 10 toward the market-only coefficients [1,0]. This asks whether the physical probability adds information beyond the price, instead of assuming every disagreement is an opportunity. It is a new small official-index calibration, not the previous rich model with momentum, asset, and clock interactions.

Report the unmodified midpoint as the forecast benchmark. Freeze fitted distributions and coefficients before September 12 onward evaluation. Fit on all valid 1–14 minute observations, market-weighted, rather than fitting only the desired price band. Numeric expiration values and outcomes are labels only. Exclude missing/noncontiguous index history; never replace missing official prices with Coinbase silently.

## Fixed execution experiment

Main user question: buy either side only when its executable ask is 55–65c inclusive, spread at most 2c, and estimated win probability exceeds cash cost (ask + quadratic taker fee + 0.25c execution allowance) by at least 3 percentage points. One contract, first qualifying executable entry per market, hold to official settlement. Consider minutes remaining 14 through 1 continuously. Do not force trades when probability is inadequate. No stop loss, protected-profit ladder, chosen time cell, or P&L-selected threshold.

Report all three models, not just a profitable one. A secondary broad-price diagnostic uses 10–90c with the same rule and must not replace the primary result after seeing P&L. Benchmark: the first 55–65c entry with no probability gate.

Subsequent minute opening quote is only an execution proxy; retain the signal probability and require the subsequent ask still meets the fixed limit and margin. Test 1c/2c additional per-contract cost and 60-second delay. Use actual archived depth where available with price/depth/receipt checks. No claim that minute candles establish fills or historical receipt latency. Fees use current published quadratic multiplier one and direct-member $0.0001 cash precision; historical changes are not reconstructed.

## Evaluation

- September 12–October 1: primary later 20-day retrospective evaluation; October 2 separately.
- Current October 3 data: short extra diagnostic; no claim of independence for previously studied windows.
- Brier score, log loss, reliability bins, and market-weighted probabilities against actual outcomes, for all prices and 55–65c.
- Trade count, win/loss, average modeled probability vs actual wins and break-even, net after fees, settlement-equity drawdown, cost/delay stress, daily P&L and day-block 90% uncertainty intervals including zero-trade days. Intervals do not correct for the entire research history.
- Check official strike against preceding index minute average and numeric expiration against official result. Record coverage and inconsistencies. Distinguish publication time assumptions in old historical APIs from causal receipt-time checks in current captures.
- Decision: a model must improve prediction beyond market prices and survive costs and separate periods before being a plausible third lane. An attractive subset found after these results requires a new future experiment; do not tune this protocol until it wins.

Save code, raw-source hashes, fitted parameters, forecasts and trade rows. Mirror the isolated study to the new server and verify both live and stable source hashes.
