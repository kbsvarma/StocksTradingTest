"""Cache-only rerun harness for backtest_databento.Backtester.

Guarantees:
  - ZERO Databento network calls: option data is served exclusively from
    backtests/ten_year/raw_dbn_cache. A cache miss raises -> the engine's per-day
    try/except turns it into a logged skip (reason prefix "error: cache-miss").
  - ZERO IBKR usage: SPX/VIX values come from provider.csv
    (recorded 09:45 values from the original stop_175/ten_year runs, plus
    cache-derived values; EOD 15:45 SPX uses the daily close as proxy).
  - Macro calendar is pre-seeded from macro_fomc_dates.json (no web fetch).

Usage:
  python3 run_cache_only.py --config CFG --output-dir DIR \
      --start-date 2020-01-01 --end-date 2026-04-30 [--max-days N]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

HERE = Path(__file__).resolve().parent
SPX_BOT = HERE.parent.parent  # spx_spread_bot/
sys.path.insert(0, str(SPX_BOT))

import databento as db  # noqa: E402

import backtest_databento as bd  # noqa: E402
from config_loader import load_config  # noqa: E402
from market_data import MacroCalendar  # noqa: E402

RAW_CACHE = SPX_BOT / "backtests" / "ten_year" / "raw_dbn_cache"
PROVIDER_CSV = HERE / "provider.csv"
MACRO_JSON = HERE / "macro_fomc_dates.json"
META_INDEX = HERE / "cache_meta_index.csv"
ET = ZoneInfo("America/New_York")


class CacheOnlyBacktester(bd.Backtester):
    """Backtester with IBKR and Databento network paths replaced by local sources."""

    def __init__(self, cfg, output_dir: Path, logger):
        # Deliberately do NOT call super().__init__ (it dials IBKR + Databento).
        self.cfg = cfg
        self.logger = logger
        self.tz = ZoneInfo(cfg.timezone)
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.raw_cache_dir = RAW_CACHE
        self.db_client = None
        self.ib = None
        self.spx_contract = "SPX"
        self.vix_contract = "VIX"

        # provider: date -> spx_entry / vix_entry / spx_eod
        prov = pd.read_csv(PROVIDER_CSV).set_index("date")
        self.provider = prov
        self.entry_time = cfg.entry_start_time()

        # macro: seed the cache file inside this run's data dir, then load (no refresh)
        data_dir = Path(cfg.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        macro_dates = json.loads(MACRO_JSON.read_text(encoding="utf-8"))
        (data_dir / "macro_calendar_cache.json").write_text(json.dumps(macro_dates), encoding="utf-8")
        self.macro = MacroCalendar(cfg, bd._MacroLogger(logger))
        if not self.macro.last_refresh_ok:
            raise RuntimeError("macro seed cache failed to load — refusing to run (fail-closed)")
        self.logger.info(
            f"cache-only mode: raw_dbn_cache={RAW_CACHE} provider_rows={len(prov)} "
            f"macro_dates={len(macro_dates)}"
        )

        # diagnostic index: date -> what the cache holds (to label misses precisely)
        mi = pd.read_csv(META_INDEX)
        self._have_def_dates = set(mi[mi.kind == "definition"]["date"])
        self._entry_sets = {}
        for r in mi[mi.kind == "entry"].itertuples():
            self._entry_sets.setdefault(r.date, []).append(frozenset(str(r.symbols).split("|")))
        self._pair_dates = set(mi[mi.kind == "pair"]["date"])

    # --- network kills -------------------------------------------------------
    def _connect_ib(self) -> None:  # pragma: no cover
        raise AssertionError("IBKR path must not be reached in cache-only mode")

    def close(self) -> None:
        pass

    def _db_get_range_df(self, **kwargs) -> pd.DataFrame:
        cache_path, payload = self._cache_path_for_query(kwargs)
        if cache_path and cache_path.exists():
            store = db.DBNStore.from_file(cache_path)
            return store.to_df()
        # precise miss label for skip accounting
        schema = payload.get("schema")
        day = str(payload.get("start"))[:10]
        if schema == "cbbo-1m":
            start = datetime.fromisoformat(payload["start"])
            end = datetime.fromisoformat(payload["end"])
            day = start.astimezone(ET).strftime("%Y-%m-%d")
            kind = "entry" if (end - start).total_seconds() <= 120 else "pair"
            if kind == "entry":
                if day in self._entry_sets:
                    raise RuntimeError("cache-miss:cbbo-entry:symbols-mismatch")
                raise RuntimeError("cache-miss:cbbo-entry:absent")
            if day in self._pair_dates:
                raise RuntimeError("cache-miss:cbbo-pair:pair-mismatch")
            raise RuntimeError("cache-miss:cbbo-pair:absent")
        if schema == "definition":
            raise RuntimeError(
                "cache-miss:definition:" + ("indexed-but-hash-miss" if day in self._have_def_dates else "absent")
            )
        raise RuntimeError(f"cache-miss:{schema}")

    # --- crash-resilient run loop ---------------------------------------------
    # Copy of bd.Backtester.run with two changes:
    #   1. daily/trade CSVs and summary.json are written via tmp-file + os.replace
    #      (atomic on POSIX) so a mid-write kill can never corrupt resume state.
    #   2. otherwise identical logic — resume semantics preserved.
    def run(self, start_date, end_date, resume, max_days, sleep_ms):
        import math
        import os
        import time as _time
        from datetime import date as _date  # noqa: F401

        daily_path = self.output_dir / "daily_results.csv"
        trades_path = self.output_dir / "trade_results.csv"

        def atomic_csv(df: pd.DataFrame, path: Path) -> None:
            tmp = path.with_suffix(path.suffix + ".tmp")
            df.to_csv(tmp, index=False)
            os.replace(tmp, path)

        daily_rows: list[dict] = []
        trade_rows: list[dict] = []
        processed_dates: set = set()

        if resume and daily_path.exists():
            old_daily = pd.read_csv(daily_path)
            if "date" in old_daily.columns:
                for d in old_daily["date"].dropna().astype(str):
                    try:
                        processed_dates.add(bd._parse_date(d))
                    except Exception:  # noqa: BLE001
                        continue
            daily_rows.extend(old_daily.to_dict(orient="records"))
            if trades_path.exists():
                old_trades = pd.read_csv(trades_path)
                trade_rows.extend(old_trades.to_dict(orient="records"))
            self.logger.info(f"resume enabled; loaded {len(processed_dates)} processed day(s)")

        days = pd.date_range(start=start_date, end=end_date, freq="D").date
        handled = 0

        for current_day in days:
            if max_days and handled >= max_days:
                break
            if current_day in processed_dates:
                continue

            handled += 1
            t0 = _time.time()
            day_record = {
                "date": current_day.isoformat(),
                "decision": "SKIP",
                "reason": "",
                "spx_entry": math.nan,
                "vix_entry": math.nan,
                "expiry": "",
                "dte": math.nan,
                "short_strike": math.nan,
                "long_strike": math.nan,
                "entry_credit": math.nan,
                "contracts": 0,
                "estimated_margin": math.nan,
                "exit_time": "",
                "exit_price": math.nan,
                "exit_reason": "",
                "pnl_per_contract": math.nan,
                "total_pnl": math.nan,
                "win_loss": "",
                "runtime_sec": 0.0,
            }

            try:
                self._evaluate_day(current_day, day_record, trade_rows)
            except Exception as exc:  # noqa: BLE001
                day_record["reason"] = f"error: {exc}"
                if not str(exc).startswith("cache-miss"):
                    self.logger.exception(f"{current_day} failed")

            day_record["runtime_sec"] = round(_time.time() - t0, 3)
            daily_rows.append(day_record)
            self.logger.info(
                f"{current_day} {day_record['decision']} {day_record['reason']} "
                f"pnl={day_record['total_pnl'] if pd.notna(day_record['total_pnl']) else 'n/a'} "
                f"({day_record['runtime_sec']}s)"
            )

            atomic_csv(pd.DataFrame(daily_rows), daily_path)
            if trade_rows:
                atomic_csv(pd.DataFrame(trade_rows), trades_path)

        daily_df = pd.DataFrame(daily_rows)
        trades_df = pd.DataFrame(trade_rows)
        summary = self._build_summary(daily_df, trades_df, start_date, end_date)
        tmp = self.output_dir / "summary.json.tmp"
        tmp.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        import os as _os

        _os.replace(tmp, self.output_dir / "summary.json")
        return daily_df, trades_df, summary

    # --- IBKR index values from provider --------------------------------------
    def _index_value(self, contract, when_et: datetime):
        ds = when_et.date().isoformat()
        if ds not in self.provider.index:
            return None
        row = self.provider.loc[ds]
        if contract == "VIX":
            v = row["vix_entry"]
            return float(v) if pd.notna(v) else None
        # SPX: entry snapshot vs EOD check
        if when_et.time() == self.entry_time:
            v = row["spx_entry"]
        else:
            v = row["spx_eod"]  # daily close as 15:45 proxy (documented)
        return float(v) if pd.notna(v) else None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--start-date", required=True)
    p.add_argument("--end-date", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--max-days", type=int, default=0)
    p.add_argument("--resume", action="store_true")
    args = p.parse_args()

    cfg = load_config(args.config)
    output_dir = Path(args.output_dir)
    logger = bd._configure_logger(output_dir)
    start_date = bd._parse_date(args.start_date)
    end_date = bd._parse_date(args.end_date)
    logger.info(
        f"[cache-only] window {start_date} -> {end_date} config={args.config} "
        f"stop_multiplier={cfg.stop_multiplier} min_credit={cfg.min_credit} "
        f"mode={'resume' if args.resume else 'fresh'}"
    )

    bt = CacheOnlyBacktester(cfg, output_dir, logger)
    daily_df, trades_df, summary = bt.run(
        start_date=start_date,
        end_date=end_date,
        resume=args.resume,
        max_days=args.max_days,
        sleep_ms=0,
    )
    logger.info(f"finished days={len(daily_df)} trades={len(trades_df)} total_pnl={summary.get('total_pnl')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
