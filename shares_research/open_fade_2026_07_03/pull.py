#!/usr/bin/env python3
"""Open-fade study: pull 30d of 5-min bars from Yahoo, one request per ticker.

BUDGET COMPLIANCE (standing repo rule after the penalty-box incident):
 - ONE history request per ticker (interval=5m period=30d in a single call).
   NOTE: the original spec asked for interval=1m, but Yahoo hard-caps 1m at
   8 days/request (verified 2026-07-05: "Only 8 days worth of 1m granularity
   data are allowed"). 30d of 1m would need 4 requests/ticker = 800 total,
   violating the budget. 5m preserves the one-request budget and still gives
   exact prices at 09:30 (bar open), 09:45 and 09:50 (bar closes).
 - Pace: sleep 0.30s between requests (< 5 req/s).
 - Abort immediately on 401/429.
 - Crash-resilient: each ticker cached to parquet on arrival; cached tickers
   skipped on restart.
"""
import os, sys, time, io, contextlib
import pandas as pd
import yfinance as yf

BASE = '/Users/varmakammili/Documents/GitHub/StocksTradingTest/shares_research/open_fade_2026_07_03'
CACHE = os.path.join(BASE, 'pull_cache')
os.makedirs(CACHE, exist_ok=True)

universe = pd.read_csv(os.path.join(BASE, 'universe.csv'))['ticker'].tolist()
log = open(os.path.join(BASE, 'pull.log'), 'a')

def logp(msg):
    print(msg, flush=True); log.write(msg + '\n'); log.flush()

consec_empty = 0
n_req = 0
for i, tk in enumerate(universe):
    fp = os.path.join(CACHE, f'{tk}.parquet')
    if os.path.exists(fp):
        continue
    buf = io.StringIO()
    err_txt = ''
    try:
        with contextlib.redirect_stderr(buf):
            df = yf.Ticker(tk).history(period='30d', interval='5m',
                                       prepost=False, auto_adjust=False)
        err_txt = buf.getvalue()
    except Exception as e:
        err_txt = buf.getvalue() + ' ' + str(e)
        df = pd.DataFrame()
    n_req += 1
    combined = err_txt.lower()
    if ('429' in combined or '401' in combined or 'too many requests' in combined
            or 'unauthorized' in combined):
        logp(f'ABORT: throttle/auth signal on {tk} after {n_req} requests: {err_txt[:300]}')
        sys.exit(2)
    if df is None or len(df) == 0:
        consec_empty += 1
        logp(f'[{i+1}/{len(universe)}] {tk}: EMPTY ({err_txt.strip()[:150]})')
        if consec_empty >= 4:
            logp('ABORT: 4 consecutive empty responses — suspected throttle. Stopping.')
            sys.exit(2)
    else:
        consec_empty = 0
        df.to_parquet(fp)
        logp(f'[{i+1}/{len(universe)}] {tk}: {len(df)} bars '
             f'{df.index[0].date()} -> {df.index[-1].date()}')
    time.sleep(0.30)

logp(f'DONE: {n_req} requests this run; '
     f'{len([f for f in os.listdir(CACHE) if f.endswith(".parquet")])} tickers cached.')
