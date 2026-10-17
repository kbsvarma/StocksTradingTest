"""Offline re-run of backtest_databento.py for the validation_apr2026 window.

2026-07-03 cleanup. Constraints honored:
  - NO Databento network calls: cache-only reads from backtests/ten_year/raw_dbn_cache.
    Cache misses raise (day recorded as error-skip). Never unlinks cache files.
  - NO IBKR required (gateway 127.0.0.1:4002 is down): SPX/VIX entry values are
    taken from the ten_year run's own daily_results.csv (recorded 2026-05-01 with
    IBKR live), and SPX@15:45 EOD-check values from ten_year/market_data/
    spx_vix_daily.csv daily close (proxy; flagged in REPORT.md).
  - Resume semantics: uses the engine's own --resume behavior (daily_results.csv
    in this dir is reloaded; days already processed are skipped). Safe to re-run
    after a crash; never starts fresh over partial output.

Run from spx_spread_bot/ :
  /opt/anaconda3/bin/python3 backtests/validation_apr2026/run_offline_rerun.py
"""
from __future__ import annotations

import json as _json
import os
import sys
from datetime import date, datetime, time as dt_time
from pathlib import Path

SPX_BOT = Path("/Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot")
OUT_DIR = SPX_BOT / "backtests" / "validation_apr2026"
CACHE_DIR = SPX_BOT / "backtests" / "ten_year" / "raw_dbn_cache"
TEN_YEAR_DAILY = SPX_BOT / "backtests" / "ten_year" / "daily_results.csv"
SPX_VIX_DAILY = SPX_BOT / "backtests" / "ten_year" / "market_data" / "spx_vix_daily.csv"
CONFIG = OUT_DIR / "config_apr2026_copy.yaml"

START = date(2026, 4, 20)
END = date(2026, 4, 30)

os.chdir(SPX_BOT)  # relative paths in config (manual_macro_dates.csv etc.)
sys.path.insert(0, str(SPX_BOT))
os.environ.setdefault("DATABENTO_API_KEY", "db-OFFLINE-CACHE-ONLY-0000000000")

import pandas as pd  # noqa: E402
import databento as db  # noqa: E402

import backtest_databento as bd  # noqa: E402
from config_loader import load_config  # noqa: E402


class _StubIB:
    def qualifyContracts(self, contract):
        return [contract]

    def reqMarketDataType(self, _):
        pass

    def isConnected(self):
        return False

    def disconnect(self):
        pass

    def reqHistoricalData(self, *a, **k):
        raise RuntimeError("IBKR offline — reqHistoricalData not available in offline rerun")


class OfflineBacktester(bd.Backtester):
    """Cache-only, IBKR-free variant. Data sources documented in module docstring."""

    def __init__(self, cfg, output_dir, logger, raw_cache_dir):
        # Load reference index values BEFORE super().__init__ (which logs).
        ref = pd.read_csv(TEN_YEAR_DAILY)
        ref = ref[ref["date"].between(START.isoformat(), END.isoformat())]
        self._entry_spx = {}
        self._entry_vix = {}
        for r in ref.to_dict(orient="records"):
            if pd.notna(r.get("spx_entry")):
                self._entry_spx[str(r["date"])] = float(r["spx_entry"])
            if pd.notna(r.get("vix_entry")):
                self._entry_vix[str(r["date"])] = float(r["vix_entry"])
        dly = pd.read_csv(SPX_VIX_DAILY)
        self._spx_close = dict(zip(dly["date"].astype(str), dly["spx_close"].astype(float)))
        super().__init__(cfg, output_dir, logger, raw_cache_dir=raw_cache_dir)

    def _connect_ib(self):
        self.ib = _StubIB()
        self.logger.info("OFFLINE MODE: IBKR stubbed (gateway 4002 down); index values from ten_year records")

    _meta_index = None

    @classmethod
    def _load_meta_index(cls, logger):
        """Index cached queries for April 2026 so we can serve requests whose
        exact hash differs but whose data is a subset of a cached response
        (the ten_year run queried call+put symbol supersets)."""
        if cls._meta_index is not None:
            return cls._meta_index
        idx = []
        for mp in CACHE_DIR.glob("*.meta.json"):
            try:
                q = _json.loads(mp.read_text())["query"]
            except Exception:
                continue
            start = str(q.get("start", ""))
            if not start.startswith("2026-04"):
                continue
            idx.append((q, mp.with_name(mp.name.replace(".meta.json", ""))))
        cls._meta_index = idx
        logger.info(f"meta index built: {len(idx)} cached April-2026 queries")
        return idx

    def _db_get_range_df(self, **kwargs):
        cache_path, payload = self._cache_path_for_query(kwargs)
        if cache_path and cache_path.exists():
            store = db.DBNStore.from_file(cache_path)
            return store.to_df()
        # Superset fallback: same dataset/schema/stype_in/start/end, symbols ⊇ requested
        want_syms = payload.get("symbols")
        want_set = set(want_syms) if isinstance(want_syms, list) else {want_syms}
        best = None
        for q, fpath in self._load_meta_index(self.logger):
            if (
                q.get("dataset") == payload.get("dataset")
                and q.get("schema") == payload.get("schema")
                and q.get("stype_in") == payload.get("stype_in")
                and str(q.get("start")) == str(payload.get("start"))
                and str(q.get("end")) == str(payload.get("end"))
            ):
                have = q.get("symbols")
                have_set = set(have) if isinstance(have, list) else {have}
                if want_set.issubset(have_set) and fpath.exists():
                    if best is None or len(have_set) < best[0]:
                        best = (len(have_set), fpath)
        if best is not None:
            self.logger.info(f"cache superset hit: {best[1].name} for {len(want_set)} requested symbol(s)")
            df = db.DBNStore.from_file(best[1]).to_df()
            if isinstance(want_syms, list) and "symbol" in df.columns:
                df = df[df["symbol"].isin(want_syms)]
            return df
        raise RuntimeError(f"raw-cache MISS (network disabled): {cache_path.name if cache_path else 'no-cache-dir'}")

    def _index_value(self, contract, when_et: datetime):
        day = when_et.date().isoformat()
        sym = getattr(contract, "symbol", "?")
        if when_et.time() <= dt_time(10, 0):  # entry snapshot (09:45)
            if sym == "SPX":
                return self._entry_spx.get(day)
            if sym == "VIX":
                return self._entry_vix.get(day)
            return None
        # EOD check (15:45): daily close as proxy (flagged in REPORT.md)
        if sym == "SPX":
            return self._spx_close.get(day)
        return None


def main() -> int:
    cfg = load_config(CONFIG)
    logger = bd._configure_logger(OUT_DIR)  # appends to existing backtest.log
    logger.info(
        f"OFFLINE RERUN starting window {START} -> {END} mode=resume "
        f"(cache-only from {CACHE_DIR}, IBKR stubbed)"
    )
    bt = OfflineBacktester(cfg, OUT_DIR, logger, raw_cache_dir=CACHE_DIR)
    try:
        daily_df, trades_df, summary = bt.run(
            start_date=START, end_date=END, resume=True, max_days=0, sleep_ms=0
        )
    finally:
        bt.close()
    logger.info(f"OFFLINE RERUN finished days={len(daily_df)} trades={len(trades_df)} total_pnl={summary.get('total_pnl')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
