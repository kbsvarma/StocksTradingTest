"""Build per-day SPX minute series cache via put-call parity.
For each trading day with Databento bid/ask data, compute SPX at each minute
using the strike-with-smallest |c-p| (closest to ATM, parity most reliable).
Output: edge_hunt_cache/spx_minute/YYYYMMDD.parquet (one per day, resumable)
Usage: python build_spx_minute.py [year_start] [year_end]
"""
import sys, re
from pathlib import Path
import pandas as pd, numpy as np

ROOT = Path(__file__).parent.parent
DATA_P = ROOT / "narrow_width_databento_2026_05_21" / "spxw_p_cbbo_1m"
DATA_C = ROOT / "narrow_width_databento_2026_05_21" / "spxw_c_cbbo_1m"
OUT_DIR = Path(__file__).parent / "spx_minute"
OUT_DIR.mkdir(exist_ok=True)

YEAR_START = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
YEAR_END = int(sys.argv[2]) if len(sys.argv) > 2 else 2026

RX = re.compile(r"[CP]0?(\d+)000$")
def _k(s): m=RX.search(s); return int(m.group(1)) if m else 0

def _load(fp):
    df = pd.read_parquet(fp)
    df = df.dropna(subset=["bid_px_00","ask_px_00"])
    df = df[(df["bid_px_00"]>0)&(df["ask_px_00"]>0)]
    if df.empty: return pd.DataFrame()
    df["ts_event"] = pd.to_datetime(df["ts_event"],utc=True).dt.tz_convert("America/New_York")
    df["ts_min"] = df["ts_event"].dt.floor("min")
    df["strike"] = df["symbol"].apply(_k)
    # Take last quote per (minute, strike)
    return df.sort_values("ts_event").groupby(["ts_min","strike"]).last()[["bid_px_00","ask_px_00"]]

def compute_spx_minute(pq, cq):
    """Vectorized per-minute SPX via parity at ATM-most strike."""
    if pq.empty or cq.empty: return pd.DataFrame()
    p = pq.reset_index(); c = cq.reset_index()
    p["pmid"] = (p["bid_px_00"]+p["ask_px_00"])/2
    c["cmid"] = (c["bid_px_00"]+c["ask_px_00"])/2
    p = p[["ts_min","strike","pmid"]]; c = c[["ts_min","strike","cmid"]]
    m = p.merge(c, on=["ts_min","strike"], how="inner")
    if m.empty: return pd.DataFrame()
    m["diff"] = (m["cmid"]-m["pmid"]).abs()
    m["spx"] = m["strike"] + m["cmid"] - m["pmid"]
    # For each minute, pick row with smallest |c-p| diff (most ATM)
    idx = m.groupby("ts_min")["diff"].idxmin()
    result = m.loc[idx, ["ts_min","strike","spx","cmid","pmid"]].set_index("ts_min").sort_index()
    result.columns = ["atm_strike","spx","atm_cmid","atm_pmid"]
    return result

p_files = sorted([f for f in DATA_P.glob("spxw_p_cbbo_*.parquet")
                  if YEAR_START <= int(f.stem.split("_")[-1][:4]) <= YEAR_END])
print(f"[spx_minute] {len(p_files)} put files for {YEAR_START}-{YEAR_END}")

done = 0; skipped = 0; failed = 0
for i, pfp in enumerate(p_files):
    ds = pfp.stem.split("_")[-1]
    out = OUT_DIR / f"{ds}.parquet"
    if out.exists():
        skipped += 1
        if (i+1) % 100 == 0: print(f"  {i+1}/{len(p_files)}  done={done} skipped(cached)={skipped} failed={failed}")
        continue
    cfp = DATA_C / pfp.name.replace("_p_","_c_")
    if not cfp.exists():
        failed += 1; continue
    try:
        pq = _load(pfp); cq = _load(cfp)
        spx_min = compute_spx_minute(pq, cq)
        if spx_min.empty:
            failed += 1; continue
        spx_min.to_parquet(out)
        done += 1
    except Exception as e:
        print(f"  FAIL {ds}: {e}")
        failed += 1
    if (i+1) % 50 == 0:
        print(f"  {i+1}/{len(p_files)}  done={done} skipped(cached)={skipped} failed={failed}")

print(f"\n[spx_minute] FINAL: done={done} skipped(cached)={skipped} failed={failed}")
print(f"             cache dir: {OUT_DIR}")
print(f"             total files: {len(list(OUT_DIR.glob('*.parquet')))}")
