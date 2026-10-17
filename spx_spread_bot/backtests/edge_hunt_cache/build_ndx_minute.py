"""Build per-day NDX minute series cache via put-call parity. Vectorized."""
import sys, re
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).parent.parent
DATA_P = ROOT / "narrow_width_databento_2026_05_21" / "ndxp_p_cbbo_1m"
DATA_C = ROOT / "narrow_width_databento_2026_05_21" / "ndxp_c_cbbo_1m"
OUT = Path(__file__).parent / "ndx_minute"
OUT.mkdir(exist_ok=True)
YS, YE = int(sys.argv[1]) if len(sys.argv)>1 else 2025, int(sys.argv[2]) if len(sys.argv)>2 else 2025

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
    return df.sort_values("ts_event").groupby(["ts_min","strike"]).last()[["bid_px_00","ask_px_00"]]

def compute(pq, cq):
    if pq.empty or cq.empty: return pd.DataFrame()
    p = pq.reset_index(); c = cq.reset_index()
    p["pmid"] = (p["bid_px_00"]+p["ask_px_00"])/2
    c["cmid"] = (c["bid_px_00"]+c["ask_px_00"])/2
    m = p[["ts_min","strike","pmid"]].merge(c[["ts_min","strike","cmid"]], on=["ts_min","strike"], how="inner")
    if m.empty: return pd.DataFrame()
    m["diff"] = (m["cmid"]-m["pmid"]).abs()
    m["ndx"] = m["strike"] + m["cmid"] - m["pmid"]
    idx = m.groupby("ts_min")["diff"].idxmin()
    r = m.loc[idx, ["ts_min","strike","ndx","cmid","pmid"]].set_index("ts_min").sort_index()
    r.columns = ["atm_strike","ndx","atm_cmid","atm_pmid"]
    return r

p_files = sorted([f for f in DATA_P.glob("ndxp_p_cbbo_*.parquet") if YS <= int(f.stem.split("_")[-1][:4]) <= YE])
print(f"[ndx_minute] {len(p_files)} put files for {YS}-{YE}")
done=skipped=failed=0
for i, pfp in enumerate(p_files):
    ds = pfp.stem.split("_")[-1]
    out = OUT / f"{ds}.parquet"
    if out.exists(): skipped+=1; continue
    cfp = DATA_C / pfp.name.replace("_p_","_c_")
    if not cfp.exists(): failed+=1; continue
    try:
        ser = compute(_load(pfp), _load(cfp))
        if ser.empty: failed+=1; continue
        ser.to_parquet(out); done+=1
    except Exception as e: print(f"FAIL {ds}: {e}"); failed+=1
    if (i+1)%50==0: print(f"  {i+1}/{len(p_files)}  done={done}")
print(f"\n[ndx_minute] FINAL: done={done} skipped={skipped} failed={failed}")
