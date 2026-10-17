"""Donchian breakout + chandelier trail replication per PREREG_TREND.md."""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "event_edge_research"))
from stats_tools import nw_tstat

FRICTION_RT = 3.50

def load_gold():
    a = pd.read_csv("../spx_spread_bot/backtests/gold_london/data/mgc_1m.csv", index_col=0)
    a.index = pd.to_datetime(a.index, utc=True)
    b = pd.read_parquet("../gold_research_2026_07/data/mgc_1m_topup_2026.parquet")
    b = b.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df = pd.concat([a[["open", "high", "low", "close", "volume"]], b]).sort_index()
    df = df[~df.index.duplicated()]
    # roll dates from MGC daily (instrument_id changes)
    d1 = pd.read_parquet("../gold_research_2026_07/data/mgc_1d_2019_2026.parquet")
    rolls = d1.index[d1["instrument_id"] != d1["instrument_id"].shift()][1:]
    df["roll_flag"] = 0
    for rd in rolls:
        df.loc[(df.index >= rd) & (df.index < rd + pd.Timedelta(days=1)), "roll_flag"] = 1
    return df, 10.0   # $ per point (MGC)

def load_fut(tag, mult):
    files = sorted(Path("../event_edge_research/data").glob(f"{tag}_ohlcv-1m_*.parquet"))
    df = pd.concat([pd.read_parquet(f) for f in files]).sort_index()
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume", "instrument_id"]]
    df = df[~df.index.duplicated()]
    df["roll_flag"] = (df["instrument_id"] != df["instrument_id"].shift()).astype(int)
    df.iloc[0, df.columns.get_loc("roll_flag")] = 0
    return df, mult

def bars(df, minutes):
    o = df["open"].resample(f"{minutes}min").first()
    h = df["high"].resample(f"{minutes}min").max()
    l = df["low"].resample(f"{minutes}min").min()
    c = df["close"].resample(f"{minutes}min").last()
    r = df["roll_flag"].resample(f"{minutes}min").max()
    B = pd.DataFrame({"o": o, "h": h, "l": l, "c": c, "roll": r}).dropna(subset=["o", "h", "l", "c"])
    B["roll"] = B["roll"].fillna(0)
    return B

def atr(B, n=20):
    tr = pd.concat([B.h - B.l, (B.h - B.c.shift()).abs(), (B.l - B.c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def run_cell(B, A, N, K, long_only, mult):
    hi = B.c.rolling(N).max().shift(1)
    lo = B.c.rolling(N).min().shift(1)
    o = B.o.values; h = B.h.values; l = B.l.values; c = B.c.values
    roll = B.roll.values
    hiv = hi.values; lov = lo.values; av = A.values
    idx = B.index
    trades = []
    pos = 0; entry = 0.0; best = 0.0; stop = 0.0; entry_i = 0
    pend = 0
    for i in range(len(B)):
        if pend != 0:   # enter at this bar's open
            pos = pend; entry = o[i]; best = o[i]
            stop = entry - pos * K * av[i - 1] if not np.isnan(av[i - 1]) else np.nan
            entry_i = i; pend = 0
            if np.isnan(stop):
                pos = 0
        if pos != 0 and roll[i] == 1:
            # force-close at prior bar close before contract roll (no basis pocketing)
            px = c[i - 1] if i > 0 else o[i]
            pnl = pos * (px - entry) * mult - FRICTION_RT
            trades.append((idx[i], pnl))
            pos = 0
        if pos != 0:
            # stop-first intrabar; gap-through-stop fills at the OPEN (realistic)
            hit = (l[i] <= stop) if pos == 1 else (h[i] >= stop)
            if hit:
                if pos == 1:
                    px = min(stop, o[i])
                else:
                    px = max(stop, o[i])
                pnl = pos * (px - entry) * mult - FRICTION_RT
                trades.append((idx[i], pnl))
                pos = 0
            else:
                if pos == 1:
                    if h[i] > best:
                        best = h[i]; stop = max(stop, best - K * av[i])
                else:
                    if l[i] < best:
                        best = l[i]; stop = min(stop, best + K * av[i])
        if pos == 0 and pend == 0 and i > N:
            if not np.isnan(hiv[i]) and c[i] > hiv[i]:
                pend = 1
            elif not long_only and not np.isnan(lov[i]) and c[i] < lov[i]:
                pend = -1
    return pd.Series(dict(trades)) if trades else pd.Series(dtype=float)

def report(tag, tr, slices):
    if len(tr) < 20:
        return None
    t, n = nw_tstat(tr.values, lags=5)
    out = dict(n=len(tr), total=tr.sum(), t=t)
    for name, (a, b) in slices.items():
        s = tr[(tr.index >= a) & (tr.index <= b)]
        out[name] = s.sum()
    return out

def main():
    data = {"MGC": load_gold(), "ES": load_fut("ES", 5.0), "NQ": load_fut("NQ", 2.0)}  # micro mults
    rows = []
    for inst, (df, mult) in data.items():
        for m in (15, 60):
            B = bars(df, m)
            A = atr(B, 20)
            for N in (20, 55):
                for K in (2.0, 3.0):
                    for lo_ in (True, False):
                        tr = run_cell(B, A, N, K, lo_, mult)
                        if len(tr) == 0: continue
                        tr.index = pd.DatetimeIndex(tr.index).tz_convert("UTC")
                        end22 = pd.Timestamp("2022-12-31", tz="UTC")
                        train = tr[tr.index <= end22]; test = tr[tr.index > end22]
                        slices = {"chop2123": (pd.Timestamp("2021-01-01", tz="UTC"), pd.Timestamp("2023-12-31", tz="UTC")),
                                  "tear2526": (pd.Timestamp("2025-01-01", tz="UTC"), pd.Timestamp("2026-07-02", tz="UTC"))}
                        r = report(f"{inst}", tr, slices)
                        if r is None: continue
                        r.update(dict(inst=inst, tf=m, N=N, K=K,
                                      dirn="L" if lo_ else "LS",
                                      train=train.sum(), test=test.sum(),
                                      n_train=len(train), n_test=len(test)))
                        rows.append(r)
            print(f"{inst} {m}m done", flush=True)
    R = pd.DataFrame(rows)
    R.to_parquet("cells.parquet")
    cols = ["inst","tf","N","K","dirn","n","total","t","train","test","chop2123","tear2526"]
    R = R.sort_values(["inst","total"], ascending=[True, False])
    print(R[cols].round(0).to_string(index=False))
    both = R[(R.train > 0) & (R.test > 0)]
    print(f"\ncells positive in BOTH halves: {len(both)}/{len(R)}")
    if len(both):
        print(both[cols].round(0).to_string(index=False))

if __name__ == "__main__":
    main()
