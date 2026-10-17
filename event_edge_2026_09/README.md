# Event edge study

Read REPORT.md for actual results and no-promotion decision. All sources/prices
are local caches or public official calendar metadata. No paid orders/live changes.

Run with /opt/anaconda3/bin/python in this workspace:
- run.py: EIA/CL price proxy study
- gold.py: CPI/MGC study
- jobs.py: employment/MGC study
- followup.py: frozen CPI-aligned rule across both event categories
- jobs_candidate.py: frozen employment rule and fill sensitivities
- assess.py: selected ledgers, bootstrap intervals, cashflow reconciliation

Source calendar files are retained under sources/. The large-impulse comparator
uses prior OBSERVED clock moves, not calendar rows containing missing weekends.
The three chosen candidate ledgers do not depend on this corrected filter.

All recent results are now inspected. No future research may present these same
segments as unseen or optimize to them while calling it independent validation.
