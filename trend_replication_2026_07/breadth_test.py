"""Breadth test per PREREG_TREND.md Extension 1. Same engine as trend_test.py,
new loaders for CL/M6E/SIL (local) + YM/ZN/BTC (pulled)."""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, ".")
from trend_test import bars, atr, run_cell, nw_tstat

# micro-equivalents / small-trader multipliers and friction per RT
# MCL: 100 bbl -> $1/0.01 = mult 100? CL price ~70 -> MCL $ per 1.0 pt = $100
# M6E: 12,500 EUR -> $1.25 per pip(0.0001) -> mult per 1.0 = $12,500
# SIL: 1,000 oz -> mult 1000; YM->MYM: $0.50/pt; ZN: full only, $1000/pt; MBT: 0.1 BTC
SPECS = {
    "CL":  dict(mult=100.0, fr=3.50),     # MCL micro crude
    "M6E": dict(mult=12500.0, fr=3.00),   # micro euro
    "SIL": dict(mult=1000.0, fr=3.50),    # micro silver
    "YM":  dict(mult=0.50, fr=3.50),      # MYM micro dow ($0.50/pt)
    "ZN":  dict(mult=1000.0, fr=4.00),    # 10y note (no micro; $15.625/tick)
    "BTC": dict(mult=0.10, fr=6.00),      # MBT micro bitcoin (0.1 BTC/pt... $0.10 per $1 move)
}

def load_local_csv(path, tzaware=True):
    df = pd.read_csv(path, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df["roll_flag"] = 0  # no instrument_id in csv caches; rolls handled via jump guard below
    return df[~df.index.duplicated()]

def load_parq_dir(pattern):
    files = sorted(Path(".").glob(pattern)) or sorted(Path("data").glob(pattern))
    df = pd.concat([pd.read_parquet(f) for f in files]).sort_index()
    df = df.rename(columns=str.lower)
    cols = ["open", "high", "low", "close", "volume"]
    if "instrument_id" in df.columns:
        df = df[cols + ["instrument_id"]]
        df = df[~df.index.duplicated()]
        df["roll_flag"] = (df["instrument_id"] != df["instrument_id"].shift()).astype(int)
        df.iloc[0, df.columns.get_loc("roll_flag")] = 0
    else:
        df = df[cols]; df = df[~df.index.duplicated()]; df["roll_flag"] = 0
    return df

def load_all():
    out = {}
    cl = pd.read_parquet("../spx_spread_bot/backtests/futures_intraday/CL_1m.parquet")
    cl = cl.rename(columns=str.lower)
    cl["roll_flag"] = (cl["instrument_id"] != cl["instrument_id"].shift()).astype(int)
    cl.iloc[0, cl.columns.get_loc("roll_flag")] = 0
    out["CL"] = cl[~cl.index.duplicated()]
    out["M6E"] = load_local_csv("../spx_spread_bot/backtests/m6e_london/data/m6e_1m.csv")
    out["SIL"] = load_local_csv("../spx_spread_bot/backtests/silver_london/data/sil_1m_2y.csv")
    for tag in ("YM", "ZN", "BTC"):
        out[tag] = load_parq_dir(f"data/{tag}_1m_*.parquet")
    return out

def main():
    data = load_all()
    rows = []
    end22 = pd.Timestamp("2022-12-31", tz="UTC")
    for inst, df in data.items():
        mult = SPECS[inst]["mult"]; fr = SPECS[inst]["fr"]
        import trend_test as T
        T.FRICTION_RT = fr
        B = bars(df, 60); A = atr(B, 20)
        for N in (20, 55):
            for K in (2.0, 3.0):
                for lo_ in (False, True):   # BOTH is primary for non-equity
                    tr = run_cell(B, A, N, K, lo_, mult)
                    if len(tr) < 30: continue
                    tr.index = pd.DatetimeIndex(tr.index).tz_convert("UTC")
                    t, n = nw_tstat(tr.values, lags=5)
                    train = tr[tr.index <= end22]; test = tr[tr.index > end22]
                    rows.append(dict(inst=inst, N=N, K=K, dirn="L" if lo_ else "LS",
                                     n=len(tr), total=tr.sum(), t=t,
                                     train=train.sum(), test=test.sum(),
                                     span=f"{tr.index[0].year}-{tr.index[-1].year}"))
        print(f"{inst} done", flush=True)
    R = pd.DataFrame(rows)
    R.to_parquet("breadth_cells.parquet")
    cols = ["inst","N","K","dirn","n","total","t","train","test","span"]
    print(R.sort_values(["inst","total"], ascending=[True,False])[cols].round(1).to_string(index=False))
    print("\nboth-halves-positive cells (gate):")
    both = R[(R.train > 0) & (R.test > 0) & (R.t >= 2)]
    print(both[cols].round(1).to_string(index=False) if len(both) else "  none")

if __name__ == "__main__":
    main()
