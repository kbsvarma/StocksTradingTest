Kalshi resting-order PAPER test — October14,2026

Primary server: varma@192.168.3.136
Directory: /home/varma/kalshi_resting_paper_2026_10_14
Service: systemctl --user status kalshi-resting-paper.service
Python: /home/varma/kalshi/venv/bin/python
Review: Python above + this directory/review.py (read-only ledger audit)
Stop isolated experiment: systemctl --user stop kalshi-resting-paper.service
Manual paper halt file: this directory/HALT_PAPER (no relation to live flags)

Read PLAN.txt and frozen_config.json before interpreting results. Primary3c and
secondary1c discounts,10second maximum,5contracts; existing lane gates/price caps
copied unchanged. Separate public-data paper observer; existing bots untouched.
No financial orders, credentials, live settings or live-service restarts.

paper.sqlite retains qualifying signal receipts, raw compressed HTTP responses,
modeled orders/fills, settlement evidence, actual-live comparison and failures.
status.json is collector health; latest_review.json points to timestamped reports.
review_state.json prevents repeated notifications. Frozen source remains under
frozen_source; preserve all records and losing variants. Schema/code changes must
be documented in CHANGELOG.txt and never silently rewrite strategy parameters.

Paper fills use subsequent aggressive prints plus visible queue, not quote touch.
This is still simulated execution. Exact hidden queue position is unknowable from
public data. Quote gaps/HTTP errors are inconclusive, not demonstrated zero fills.
First signal per market means the resting variants intentionally have no fallback.
Report missed live winners and total net, not only improvements on fills obtained.

At14-day new-entry cutoff, wait for outcomes and issue final/insufficient-sample
report. Then stop/disable only this experiment service and delete its heartbeat.
Do not promote to live or alter the original bot automatically.
