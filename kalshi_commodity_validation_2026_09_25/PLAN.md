# Frozen validation plan
Window September 11–24 UTC, 2026. Assets GOLD, WTI, SILVER. Reuse ask >=85c and <93c, spread <=2c, remaining 3–10min, z>=2 from 60 completed minute returns, two contracts, one entry per market, capped IOC, hold to settlement. Actual Pyth settlement-matched history required for exact signal replay.

Pyth authenticated history returned HTTP401 and no Pyth credentials were found in server environment or home env files. Existing metals archives store market quotes, not underlying Pyth minute prices. Do not claim a full strategy backtest without underlying data.

Run a separately labeled quote-only baseline as a market feasibility diagnostic, OMITTING z. First valid decision; next candle opening quote must be within unchanged capped/tick-rounded limit. Two contracts; series fee multiplier; 1c per-contract extra cost stress. Full period and chronological halves. This baseline cannot validate or invalidate the z-filtered strategy. No parameter tuning, no live changes, no order placement.
