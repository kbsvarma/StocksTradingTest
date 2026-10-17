"""Eurex mechanism pulse per PREREG_EUREX_PULSE.md — same window, same
engine, in-family envelope verdict. NOT seat-earning by construction."""
import glob
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

BASE = Path(__file__).parent
ROOT = BASE.parent
START = pd.Timestamp("2025-03-11", tz="UTC")
END = pd.Timestamp("2026-06-30 23:59", tz="UTC")
CET, ET = ZoneInfo("Europe/Berlin"), ZoneInfo("America/New_York")

INSTR = {   # sym: (per-point value in native ccy, RT friction, gate tz+lo/hi)
    "FDAX": (1.0, 2.50, ("CET", 1, 5)),
    "FESX": (1.0, 2.50, ("CET", 1, 5)),
    "NQ":   (2.0, 3.50, ("ET", 2, 6)),
    "ES":   (5.0, 3.50, ("ET", 2, 6)),
    "YM":   (0.5, 3.50, ("ET", 2, 6)),
}


def load_eurex(sym):
    df = pd.read_parquet(BASE / f"data/{sym}_1m.parquet")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    # roll days from the DEFINITION schema (session-scoped XEUR
    # instrument_ids are useless for contract identity — first two
    # attempts flagged 6,422 then 555 bars; truth is 6 quarterly rolls
    # on the expiry Fridays, verified from raw_symbol/expiration)
    d = pd.read_parquet(BASE / "data/definitions.parquet")
    g = d[d["symbol"] == f"{sym}.v.0"]
    per_day = g.groupby(g.index.normalize())["raw_symbol"].last()
    roll_days = per_day[per_day != per_day.shift()].index[1:]
    df["roll_flag"] = 0
    for rd in roll_days:
        df.loc[(df.index >= rd) & (df.index < rd + pd.Timedelta(days=1)),
               "roll_flag"] = 1
    return df


def load_us(sym):
    pats = {"NQ": ROOT / "event_edge_research/data/NQ_ohlcv-1m_*.parquet",
            "ES": ROOT / "event_edge_research/data/ES_ohlcv-1m_*.parquet",
            "YM": ROOT / "trend_replication_2026_07/data/YM_1m_*.parquet"}
    files = sorted(glob.glob(str(pats[sym])))
    df = pd.concat([pd.read_parquet(f) for f in files]).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    df = df[(df.index >= START - pd.Timedelta(days=90)) & (df.index <= END)]
    df["roll_flag"] = (df["instrument_id"] != df["instrument_id"].shift()
                       ).astype(int)
    df.iloc[0, df.columns.get_loc("roll_flag")] = 0
    return df


def to_60m(df):
    g = df.resample("60min", label="left")
    B = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                      "low": g["low"].min(), "close": g["close"].last(),
                      "roll": g["roll_flag"].max()}).dropna(subset=["close"])
    B["roll"] = B["roll"].fillna(0).astype(int)
    return B


def simulate(B, sym):
    mult, fr, (tz, lo, hi) = INSTR[sym]
    pc = B["close"].shift(1)
    tr_ = pd.concat([B["high"] - B["low"], (B["high"] - pc).abs(),
                     (B["low"] - pc).abs()], axis=1).max(axis=1)
    atr = tr_.rolling(20).mean()
    donch = B["close"].rolling(20).max().shift(1)
    ap = atr / B["close"]
    th = ap.rolling(24 * 365, min_periods=24 * 60).quantile(0.95)
    zone = CET if tz == "CET" else ET
    hours = B.index.tz_convert(zone).hour
    sess_ok = ~((hours >= lo) & (hours < hi))
    sig = ((B["close"] > donch) & ~(ap > th) & atr.notna()
           & np.asarray(sess_ok)).values
    O, H, L, C = (B[c].values for c in ("open", "high", "low", "close"))
    A, R, ts = atr.values, B["roll"].values, B.index
    trades, pos, pend = [], 0, 0
    e_px = st = hw = 0.0
    e_ts = None
    for i in range(1, len(B)):
        if pend:
            e_px, e_ts, pos, pend, hw = O[i], ts[i], 1, 0, O[i]
            st = e_px - 2.0 * A[i - 1]
        if pos:
            if R[i]:
                x = C[i - 1]
                trades.append((e_ts, ts[i], e_px, x,
                               (x - e_px) * mult - fr, "roll"))
                pos = 0
            elif L[i] <= st:
                x = min(st, O[i])
                trades.append((e_ts, ts[i], e_px, x,
                               (x - e_px) * mult - fr, "stop"))
                pos = 0
            else:
                hw = max(hw, H[i])
                st = max(st, hw - 2.0 * A[i])
        if not pos and not pend and sig[i] and not R[i]:
            pend = 1
    T = pd.DataFrame(trades, columns=["entry_time", "exit_time", "entry_px",
                                      "exit_px", "pnl", "reason"])
    return T[T["entry_time"] >= START].reset_index(drop=True)


def stats(T, ccy):
    if not len(T):
        return {}
    w = T[T["pnl"] > 0]
    l = T[T["pnl"] <= 0]
    eq = T.set_index("exit_time")["pnl"].cumsum()
    dd = float((eq - eq.cummax()).min())
    top3 = T["pnl"].nlargest(3).sum()
    gross_pos = w["pnl"].sum()
    m = T.set_index("exit_time")["pnl"].resample("ME").sum()
    losing_m = m[m < 0]
    return dict(n=len(T), win=len(w) / len(T) * 100,
                payoff=(w["pnl"].mean() / abs(l["pnl"].mean())
                        if len(w) and len(l) else np.nan),
                total=T["pnl"].sum(), maxdd=dd,
                top3_share=(top3 / gross_pos * 100 if gross_pos > 0
                            else np.nan),
                rent=losing_m.mean() if len(losing_m) else 0.0, ccy=ccy)


def main():
    rows = {}
    for sym in INSTR:
        df = load_eurex(sym) if sym in ("FDAX", "FESX") else load_us(sym)
        B = to_60m(df)
        B = B[B.index <= END]
        T = simulate(B, sym)
        rows[sym] = stats(T, "EUR" if sym in ("FDAX", "FESX") else "USD")
    print(f"{'sym':<6}{'n':>4}{'win%':>7}{'payoff':>8}{'total':>10}"
          f"{'maxDD':>10}{'top3%':>7}{'rent/mo':>9}  ccy")
    for s, r in rows.items():
        print(f"{s:<6}{r['n']:>4}{r['win']:>7.1f}{r['payoff']:>8.2f}"
              f"{r['total']:>10,.0f}{r['maxdd']:>10,.0f}"
              f"{r['top3_share']:>7.0f}{r['rent']:>9,.0f}  {r['ccy']}")
    us = [rows[s] for s in ("NQ", "ES", "YM")]
    env = lambda k: (min(r[k] for r in us), max(r[k] for r in us))
    wlo, whi = env("win")
    plo, phi = env("payoff")
    tlo, thi = env("top3_share")
    print(f"\nUS envelope: win {wlo:.0f}-{whi:.0f}%  payoff {plo:.2f}-"
          f"{phi:.2f}  top3 {tlo:.0f}-{thi:.0f}%")
    for s in ("FDAX", "FESX"):
        r = rows[s]
        ok_w = wlo - 8 <= r["win"] <= whi + 8
        ok_p = plo * 0.6 <= r["payoff"] <= phi * 1.4
        ok_t = tlo - 15 <= r["top3_share"] <= thi + 15
        verdict = "IN-FAMILY" if (ok_w and ok_p and ok_t) else "OUTSIDE"
        print(f"{s}: win {'ok' if ok_w else 'OUT'}, payoff "
              f"{'ok' if ok_p else 'OUT'}, top3 "
              f"{'ok' if ok_t else 'OUT'} -> {verdict}")


if __name__ == "__main__":
    main()
