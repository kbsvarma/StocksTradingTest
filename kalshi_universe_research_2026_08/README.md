# Independent Kalshi Universe Research

This directory is isolated from every live bot. Existing reports are treated
as hypotheses only; calculations here start from raw market/candle records.

Research rules:

1. One trade per independent event for each strategy configuration.
2. Signal on one archived quote and execute no earlier than the next quote.
3. Charge the quadratic fee and report an additional one-cent adverse fill.
4. Split whole settlement clusters chronologically into 50/25/25 percent.
5. Resample whole settlement clusters, not correlated strikes.
6. Record every tested configuration and apply Benjamini-Hochberg correction.
7. A historical candidate is not live-ready without prospective executable
   fills and a positive clustered lower bound.

The primary scripts are:

- `inventory_report.py`: current recurring-series census.
- `fixed_expiry_study.py`: event-deduplicated price-path strategies.
- `weather_structure_study.py`: weather-only calibration and basket tests.
- `candidate_validation.py`: drawdown, overlap, stress, and correlation audit.
- `sports_liveness_audit.py`: actual live-rule sports replay.
- `weather_observation_lock.py`: deterministic ASOS source-lock test.
- `weather_path_state_study.py`: observed-high path-state search.
- `cs2_independent_study.py`: scheduled-start, causal CS2 study.
- `metals_forward_audit.py`: five-day Gold/Silver/WTI transfer audit.
- `zero_loss_uncertainty.py`: unseen-loss uncertainty for all-win samples.
- `structural_scan_all.py`: read-only guaranteed-payoff scan.

The consolidated evidence and deployment verdict are in `REPORT.md`.
