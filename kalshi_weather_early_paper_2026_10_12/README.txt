STATUS: CANCELED BY USER 2026-10-14. Service stopped and disabled; scheduled review deleted.
Data and results preserved in closed_2026_10_14/REPORT.txt. Do not restart without new authorization.

WEATHER EARLY-STATION PROSPECTIVE PAPER TEST
Authorized October12,2026 ET. Existing trading bots remain unchanged.

This observer uses public GET requests only and loads no trading credentials.
It records raw member-station readings and books for Miami, Chicago, LA Coastal
and NYC hourly markets. All simulated orders are five contracts. No exchange
order is sent. Full frozen strategy, timing and statistical limitations: PLAN.txt.

Primary server: varma@192.168.3.136
Directory: /home/varma/kalshi_weather_early_paper_2026_10_12
Service: kalshi-weather-early-paper.service (user systemd)
Readout: status.json, refreshed every5seconds
Receipts and paper ledger: paper.sqlite, SQLite WAL
Use a read-only SQLite backup for consistent reviews, never copy an active WAL
database file alone. Every GET payload/version is compressed and timestamped.
Config: frozen_config.json; residuals and training IDs: training.json

Compare three independent $100 paper portfolios: early (3seconds after decision),
delay60, and the first observed canonical publication of the same source minute.
Same selected strike/side and original cap for paired execution comparisons.
Each requires sufficient five-contract depth in two successive books, one second
apart. Report fees plus1c/contract friction (2c for delay60). Hold to authoritative
settlement; pending P&L is not counted as realized. No fee credits are assumed.
First-observed publication and source arrival are polling bounds, not exact
exchange publication timestamps. Displayed liquidity is not an actual fill.

The model is a new forward candidate, not the historical +$7.07 sensitivity.
Actual source-to-expiry horizon replaces assumed historical publication delays.
Training uses prior settled data ending October12 04:00UTC and never learns
from the paper outcomes. All four cities retained. No tuning or silent variant
selection. Historical QC was not used to select training rows. Pending readings
can later be revised; mismatches and losses remain in the evidence.

First preliminary review at20 settled early trades. Main review needs14 days
and100 early settlements; these are minimum sample gates, not proof of an edge.
At14days stop entries and finish open holdings, then end collection. Insufficient
trades or execution evidence means insufficient evidence, not a pass.
The observer runs independently of this desktop session.

Follow-up: review-early-weather-station-paper-test, hourly in this chat. Report
first fills and first settled results, then material findings and sample gates;
stay quiet on unchanged healthy/pending checks. Milestones are tracked in
review_state.json. No automatic real-money promotion or source-model retuning.
review.py creates a consistent read-only SQLite backup for its receipt audit.
Routine temporary review copies are removed after successful auditing; original
records stay in paper.sqlite. Use --keep-snapshot for milestone evidence.

Operational controls affect ONLY this experiment:
Create HALT_PAPER here to prevent new signals; existing paper attempts/holdings
finish. systemctl --user stop kalshi-weather-early-paper.service stops collection.
Restart refuses frozen code/config drift; it preserves original ledger ownership
and fails attempts whose execution deadlines elapsed. It never replays a missed
signal from old observations. Do not overwrite paper.sqlite with a review copy.

Primary API references:
https://docs.kalshi.com/api-reference/live-data/get-weather-index
https://docs.kalshi.com/api-reference/live-data/get-weather-index-calibrations
https://docs.kalshi.com/api-reference/market/get-multiple-market-orderbooks
https://docs.kalshi.com/getting_started/fee_rounding
