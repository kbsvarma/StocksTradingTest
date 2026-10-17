# Hourly Kalshi Research

This package independently studies Kalshi hourly crypto and commodity strike
ladders. It is isolated from all live bots.

- `PREREG.md`: frozen strategy surface and promotion criteria.
- `pull_hourly.py`: public API market/candle archive pull.
- `study_hourly.py`: causal event-level replay and statistics.
- `test_study_hourly.py`: execution and statistical helper tests.
- `REPORT.md`: human-readable conclusions.
- `results/hourly_study.json`: machine-readable complete result.

The current research verdict is to reject generic hourly crypto and retain
only Gold exact-5m as a locked forward candidate. See `REPORT.md` for the
evidence and limitations.
