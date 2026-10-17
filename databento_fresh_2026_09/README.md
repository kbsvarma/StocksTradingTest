# Fresh Databento track

Read REPORT.md for conclusions and limitations; PROTOCOL.md records initial
hypotheses and follow-up declarations. Previous Trend bot research remains parked.

Source inventory: file_inventory.json, catalogue*.csv/json, coverage_by_folder.csv,
source_fingerprints.csv, deduplication.json, futures_coverage.csv, ice_audit.csv.
Data derivatives are under data/, complete positive and negative scores and daily
paths under results/. No downloaded source data was modified.

Replay order using /opt/anaconda3/bin/python:
1. build_futures.py
2. repair_exits.py
3. intraday.py; daily_designs.py; stocks.py; options.py; innovation.py; ice.py
4. followup.py
5. hybrid.py; uncertainty.py; controls.py

Scripts use local caches only. Future replays must preserve the missing-exit,
roll and publisher caveats in REPORT.md. Data building resets futures snapshots,
so repair_exits.py must follow build_futures.py before intraday.py.

Research checkpoint: broad screen and connected follow-up completed. Copper/silver
and ICE commodity ensembles are research leads only; recent blend roughly flat,
negative under stress. No design promoted. Final-calendar scores are now exposed:
do not reuse them as unseen validation or silently retune a candidate to them.
The multiple-search adjustment covered daily-family trials only, not all history.

Control contribution file hybrid_ice_contributions.csv uses actual hybrid weights.
The all-scoreboard period may start differently across families; matched comparisons
are in hybrid_scoreboard.csv and the report table. No comparison to Ridge is made.
