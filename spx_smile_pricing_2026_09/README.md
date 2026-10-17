# SPXW smile-pricing tests

Read REPORT.md for the authoritative extended-optimizer run. PROTOCOL.md records
rules fixed before the initial test; NUMERICAL_REFINEMENT.md documents the sole
post-result numerical change. This is historical exploratory research, not a
pristine holdout or a live deployment.

- run.py: raw-cache merge, strike cross-fitting, flat/SSVI/SVI calibration, replay.
- analyze.py: matched pricing comparisons and day-cluster bootstrap summaries.
- verify.py: independent raw-quote cashflow reconciliation and output invariants.
- results/: authoritative predictions, fits, trades, audit, manifest, summaries.
- results_initial_180/, REPORT_initial_180.md, run_initial_180.py: preserved first run.
- smoke/: first three local dates, rejected by preset training-coverage thresholds.

Commands (from repository root):

    /opt/anaconda3/bin/python spx_smile_pricing_2026_09/run.py --selftest
    /opt/anaconda3/bin/python spx_smile_pricing_2026_09/run.py --workers 4
    /opt/anaconda3/bin/python spx_smile_pricing_2026_09/analyze.py
    /opt/anaconda3/bin/python spx_smile_pricing_2026_09/verify.py

No live bot files, existing data, or external accounts are changed by these scripts.
