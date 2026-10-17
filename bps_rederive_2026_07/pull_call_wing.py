"""Pull the SPXW CALL WING (spot .. spot+4%) for every cached 0DTE day.

The 2013-2026 cache was bought for bull PUT spreads: strikes span roughly
spot-8% .. spot+0%, so an iron condor's short call wing is absent. This
adds only the missing upper strikes.

Cost control:
  - OCC symbols are CONSTRUCTED on the 5-pt grid, not discovered via a
    per-day definition pull (validated: all 35 requested contracts return).
    ~$0.0021/day instead of ~$0.026/day.
  - Requests are BATCHED BY MONTH: one range call carrying every 0DTE call
    symbol for that month, then split per day on write. ~150 requests
    instead of ~4,200, which is the difference between minutes and hours.
  - HARD STOP at $100 cumulative.
Resumable: days already on disk are skipped; a month whose days are all
present is not requested at all.
"""
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import databento as db
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import chain                                                    # noqa: E402

KEY = os.environ["DATABENTO_API_KEY"]
BUDGET_ALERT = 100.0
OUT = HERE / "data/call_wing"
OUT.mkdir(parents=True, exist_ok=True)

c = db.Historical(KEY)
spx = pd.read_csv(HERE / "data/spx_daily_cboe.csv",
                  parse_dates=["DATE"]).set_index("DATE")["SPX"]

tags = sorted({f.stem.replace("spxw_p_cbbo_", "")
               for f in (chain.BASE / "spxw_p_cbbo_1m").glob("*.parquet")})
tags = [t for t in tags if t.isdigit() and len(t) == 8]
todo = [t for t in tags if not (OUT / f"spxw_c_cbbo_{t}.parquet").exists()]
months = defaultdict(list)
for t in todo:
    months[t[:6]].append(t)
print(f"0DTE days: {len(tags)} | on disk: {len(tags)-len(todo)} | "
      f"to pull: {len(todo)} across {len(months)} months", flush=True)

spent = 0.0
ok = skip = fail = 0
t0 = time.time()
for mi, (mon, mtags) in enumerate(sorted(months.items())):
    syms, per_day = [], {}
    for tag in mtags:
        d = pd.Timestamp(tag)
        prev = spx[spx.index < d]
        if not len(prev):
            skip += 1
            continue
        center = float(prev.iloc[-1])
        lo = int(center // 5 * 5)
        hi = int(center * 1.04 // 5 * 5) + 5
        s = [f"SPXW  {d.strftime('%y%m%d')}C{k*1000:08d}"
             for k in range(lo, hi + 1, 5)]
        per_day[tag] = set(s)
        syms.extend(s)
    if not syms:
        continue
    start = f"{mon[:4]}-{mon[4:6]}-01T13:25"
    last = pd.Timestamp(max(mtags)) + pd.Timedelta(days=1)
    kw = dict(dataset="OPRA.PILLAR", symbols=sorted(set(syms)),
              stype_in="raw_symbol", schema="cbbo-1m",
              start=start, end=last.strftime("%Y-%m-%dT00:00"))
    try:
        cost = float(c.metadata.get_cost(**kw))
    except Exception as e:
        print(f"[{mon}] cost failed: {str(e)[:90]}", flush=True)
        fail += len(mtags)
        continue
    if spent + cost > BUDGET_ALERT:
        print(f"\n{'='*60}\n!! BUDGET STOP at ${spent:.2f}; next month costs "
              f"${cost:.3f}.\n   {len(todo)-ok-skip} days unpulled.\n{'='*60}",
              flush=True)
        break
    try:
        df = c.timeseries.get_range(**kw).to_df()
    except Exception as e:
        print(f"[{mon}] pull failed: {str(e)[:90]}", flush=True)
        fail += len(mtags)
        continue
    spent += cost
    if len(df) == 0:
        skip += len(mtags)
        continue
    keep = [x for x in ("symbol", "bid_px_00", "ask_px_00",
                        "bid_sz_00", "ask_sz_00") if x in df.columns]
    df = df[keep]
    et_date = df.index.tz_convert("America/New_York").date
    for tag in mtags:
        want = per_day.get(tag)
        if not want:
            continue
        sub = df[(pd.Series(et_date, index=df.index)
                  == pd.Timestamp(tag).date()) & df["symbol"].isin(want)]
        if len(sub) == 0:
            skip += 1
            continue
        sub.to_parquet(OUT / f"spxw_c_cbbo_{tag}.parquet")
        ok += 1
    el = time.time() - t0
    print(f"  [{mon}] {len(mtags)}d ${cost:.4f} | cum ${spent:.3f} | "
          f"ok={ok} skip={skip} fail={fail} | {mi+1}/{len(months)} months | "
          f"{el/60:.1f}min elapsed", flush=True)

print(f"\nDONE pulled={ok} skipped={skip} failed={fail}  "
      f"TOTAL SPENT ${spent:.2f}", flush=True)
