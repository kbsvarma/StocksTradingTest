# DOGE 15-minute investigation

Frozen before replay: examine September 1–14, 2026 (UTC), or all available markets within that interval. Reuse the existing strategy without optimizing: favorite ask >=85c and <93c, spread <=2c, remaining time 3–10 minutes, z >=2 using Coinbase pre-open close and the last 60 completed log returns. One contract per market, next-minute opening ask proxy within signal+1c and below 93.9c, hold to settlement. Report fees and additional 1c slippage sensitivity. Require consecutive observed Coinbase bars; no gap filling in the main result. Diagnose no-trade missing-minute gaps separately.

Report market count, volume, data coverage, signal counts, trade frequency, fee-adjusted P&L, wins/losses, drawdown and chronological halves. Historical quote replay does not establish executable depth or guaranteed IOC fills. Do not add DOGE to live trading from this investigation.
