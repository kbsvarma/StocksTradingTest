"""One-time seed of the 60m bar store from local Databento caches.

Sources (all real exchange data, pulled during the research program):
  NQ/ES : event_edge_research/data/{SYM}_ohlcv-1m_YYYY.parquet   (2010→2026-07-02)
  YM    : trend_replication_2026_07/data/YM_1m_YYYY.parquet      (2016→2026-07-02)
  GC    : spx_spread_bot/backtests/gold_london/data/mgc_1m.csv   (2019-07→2026-05-05)
          + gold_research_2026_07/data/mgc_1m_topup_2026.parquet (→2026-07-02)

Resample = 60min, label LEFT (identical to the backtest engines). The live
feed appends micro-contract bars (MNQ/MES/MYM/MGC) after the seed end — same
index points as the seed contracts; the seam is at 2026-07-03 00:00 UTC.
"""
from __future__ import annotations

import glob
import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
REPO = BASE.parent


def resample_60m(df: pd.DataFrame) -> pd.DataFrame:
    g = df.resample("60min", label="left")
    out = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(),
        "low": g["low"].min(), "close": g["close"].last(),
        "volume": g["volume"].sum(),
    }).dropna(subset=["open", "high", "low", "close"])
    return out


def load_parquets(pattern: str) -> pd.DataFrame:
    files = sorted(glob.glob(str(pattern)))
    if not files:
        raise FileNotFoundError(pattern)
    df = pd.concat([pd.read_parquet(f) for f in files]).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df


def seed_core(sym: str, pattern: Path) -> pd.DataFrame:
    df = load_parquets(str(pattern))
    return resample_60m(df[["open", "high", "low", "close", "volume"]])


def seed_gold() -> pd.DataFrame:
    csv = REPO / "spx_spread_bot/backtests/gold_london/data/mgc_1m.csv"
    d1 = pd.read_csv(csv)
    d1["timestamp"] = pd.to_datetime(d1["timestamp"], utc=True)
    d1 = d1.set_index("timestamp")
    d2 = pd.read_parquet(REPO / "gold_research_2026_07/data/mgc_1m_topup_2026.parquet")
    d2 = d2[["open", "high", "low", "close", "volume"]]
    d2.index = d2.index.tz_convert("UTC")
    d1 = d1[d1.index < d2.index.min()]
    df = pd.concat([d1, d2]).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return resample_60m(df)


def main():
    sys.path.insert(0, str(BASE))
    from data import BarStore

    store = BarStore(BASE / "state")
    jobs = {
        "NQ": lambda: seed_core("NQ", REPO / "event_edge_research/data/NQ_ohlcv-1m_*.parquet"),
        "ES": lambda: seed_core("ES", REPO / "event_edge_research/data/ES_ohlcv-1m_*.parquet"),
        "YM": lambda: seed_core("YM", REPO / "trend_replication_2026_07/data/YM_1m_*.parquet"),
        "GC": seed_gold,
    }
    for sym, fn in jobs.items():
        p = store.path(sym)
        if p.exists():
            print(f"{sym}: store exists, skipping ({p})")
            continue
        B = fn()
        n = store.append(sym, B, source="seed")
        print(f"{sym}: seeded {n:,} bars  {B.index[0]} .. {B.index[-1]}")


if __name__ == "__main__":
    main()
