WEATHER RESEARCH REBUILD — October12,2026

All artifacts are isolated from production. No financial orders, deposits,
transfers, activation flags, strategy settings or services are changed.

Canonical full data/work directory on new server:
/home/varma/kalshi_weather_rebuild_2026_10_12
Local code, plans, results and receipts:
/Users/varmakammili/Documents/GitHub/StocksTradingTest/kalshi_weather_rebuild_2026_10_12

Existing dependencies: /home/varma/kalshi/venv/bin/python, numpy, requests.
Inputs preserved in earlier broad review/weather validation/daily market-wide
archives. Full new candle, raw response, feature and station-detail archives
remain on the new server. Local copy omits the large regenerable feature tables
and some raw responses; it is not an assertion that all data lives locally.

Frozen plans by chronological research stage:
PLAN.txt — weekly market-anchored temperature model and market-only control.
SHORT_HOLD_PLAN.txt — five-minute continuation/reversal/temperature confirmation.
FORECAST_PLAN.txt — archived NBS forecasts plus observed weather, daily highs.
PENDING_PLAN.txt — final-five-minute pending-station nowcast.
Do not replace failing results with a newly tuned variant under the same name.

Offline replays (after collecting their inputs):
OPENBLAS_NUM_THREADS=1 python rebuild.py --extension
OPENBLAS_NUM_THREADS=1 python short_hold.py --extension
OPENBLAS_NUM_THREADS=1 python daily_forecast.py
OPENBLAS_NUM_THREADS=1 python pending_replay.py
python verify.py

GET-only collection scripts: pull_extension.py, pull_forecasts.py,
pull_pending_history.py, pending_feed_probe.py. The Kalshi scripts deliberately
yield the first35seconds of each minute to existing trading. Do not run them
all concurrently or remove their pacing. No daemon is installed by these files.

Publication caveat: a model run/observation timestamp or vendor receipt time
does not prove historical public first arrival. Explicit lag assumptions and
stress tests do not replace a prospective feed-and-book recording experiment.
Completed-minute quotes likewise do not prove five contracts could be filled.
