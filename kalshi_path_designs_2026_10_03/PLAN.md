# New path-dependent BTC/ETH entry designs

October 3, 2026. User requests an independently designed strategy producing materially more trades. Research only; preserve the two live lanes and immutable September checkpoint. No permission questions needed for this offline work. No live activation is requested.

Hypotheses use information available by the decision minute:

1. Persistence: a moderate cushion held over two or three consecutive completed minutes may be more useful than a single high-z spike. Test signed spot advance as an additional confirmation.
2. Pullback/recovery: a market previously had z>=2 and ask>=85c, then offers the same side at least 3/5/8c cheaper within five minutes while retaining a positive cushion. Test a last-minute spot recovery confirmation. Do not use later observations to arm earlier entries.
3. Cross-asset confirmation: BTC and ETH both favor the same settlement direction, with positive normalized cushions, may filter weaker entries. This is correlated evidence, not independent confirmation.
4. Quote lag: spot advances at least 0.75/1.25 historical minute standard deviations over three minutes while the same-side Kalshi ask increases no more than 0/2c. A cheaper positive-cushion entry might monetize delayed repricing.

Freeze a finite, documented grid in configurations.json, BTC/ETH/both. Existing 85–95c and broader 65/75c entry floors, ceiling95c, hardcap93.9c, full five-contract depth, spread<=2c. These are distinct entry mechanisms, not new execution permissions. One entry per market, hold to settlement, shared20/$19/$10 and both original lanes retained. Candidate reserve95c per contract even at cheaper prices. No exits, leverage, or adding to an existing market in this study.

Use the existing raw-input August13–October2 archive; this history is reused and not an untouched holdout. Select on August13–27 and August28–September11; then freeze candidates before evaluating September12–October1 and October2. Primary costs modeled fees plus1c/contract; double-cost, limit-price, earlier recorded books, minimum10s book delay and outcome-independent fill misses as robustness checks. Full quantity depth must be rebuilt, not inferred from quote volume.

Screen with current five-contract candidate size. Prefer configurations with positive portfolio contribution AND positive standalone candidate attribution in both early periods; at least25% extra entries in both early periods; positive contribution at2c stress and no more than$10 extra historical drawdown per period. Keep failed families. Freeze up to two distinct asset/time/price variants per family from early results, plus highest-frequency positive-profit controls if different. Choose primary after early recorded-book checks, before later results. If none passes, report the failed gate and any later choice as secondary exploratory selection.

For frozen finalists also test one/two-contract experimental entries, keeping the existing two lanes at five contracts. Quantity changes require full depth, fees and risk recalculation. This measures activity versus capacity consumption; do not equate more small entries with more profit. All results must show baseline displacement, candidate standalone and incremental returns, W/L, drawdown and chronology. Validate exact302/+65.9172 baseline regression, causal feature construction, fee/depth and actual production risk-guard parity for any recommended one/two/five-contract candidate.
