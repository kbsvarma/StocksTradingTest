"""Offline (cache-only) replay of backtest_jade_lizard.py — ZERO network calls.

Why this wrapper exists
-----------------------
backtest_jade_lizard.py as written cannot run here:
  1. It requires DATABENTO_API_KEY + live Databento fallback on cache miss
     (keys are locked; network use is forbidden for this study).
  2. It requires a live IBKR connection for SPX/VIX values (_index_value).
  3. Its main() passes daily_csv_path= to Backtester.__init__ and reads
     args.daily_csv — neither exists in backtest_databento.py (TypeError /
     AttributeError). We bypass main() entirely.

Offline substitutions (all documented in REPORT.md):
  - Databento: exact query-hash cache hit first; otherwise the request is
    COMPOSED from other cached DBN files whose symbol set / time window is a
    superset (per-symbol coverage). If any leg cannot be served locally the
    day errors out and is counted — never silently proxied, never network.
  - SPX/VIX at 09:45 entry: exact values recorded by the original ten-year
    BPS run (backtests/ten_year/daily_results.csv spx_entry/vix_entry).
  - SPX at the 15:45 EOD check: approximated with the official daily close
    (backtests/ten_year/market_data/spx_vix_daily.csv). Only used for the
    ">50pt from both strikes" classification.
  - Macro-event filter: replayed from the original run's recorded skip
    reasons (exact same FOMC-day exclusions).

Run:
  /opt/anaconda3/bin/python3 backtests/jade_lizard_2026_07_03/run_offline_jl.py \
      [--start 2013-04-01] [--end 2026-04-30] [--max-days 0]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # spx_spread_bot/
sys.path.insert(0, str(REPO))

import databento as db  # noqa: E402  (local file reads only)

import backtest_databento as bd  # noqa: E402
import backtest_jade_lizard as jl  # noqa: E402
from config_loader import load_config  # noqa: E402

TEN_YEAR = REPO / "backtests" / "ten_year"
CACHE_DIR = TEN_YEAR / "raw_dbn_cache"
INDEX_PARQUET = HERE / "cache_index.parquet"


class OfflineCacheMiss(RuntimeError):
    pass


class MacroReplay:
    """Replays the macro-filter decisions recorded by the original run."""

    def __init__(self, blocked: dict[date, str]):
        self.blocked = blocked

    def refresh(self) -> None:  # pragma: no cover
        pass

    def has_blocking_event(self, day: date):
        ev = self.blocked.get(day)
        return (ev is not None), (ev or "")


class OfflineJL(jl.JadeLizardBacktester):
    """JadeLizardBacktester with all network I/O replaced by local lookups."""

    def __init__(self, cfg, output_dir: Path, logger, stats: dict):
        # deliberately NOT calling super().__init__ (it dials IBKR + Databento)
        self.cfg = cfg
        self.logger = logger
        self.tz = ZoneInfo(cfg.timezone)
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.raw_cache_dir = CACHE_DIR
        self.db_client = None
        self.ib = SimpleNamespace(isConnected=lambda: False)
        self.spx_contract = SimpleNamespace(symbol="SPX")
        self.vix_contract = SimpleNamespace(symbol="VIX")
        self.stats = stats
        self._df_lru: OrderedDict[str, pd.DataFrame] = OrderedDict()

        # ── offline index of the raw cache ────────────────────────────────
        idx = pd.read_parquet(INDEX_PARQUET)
        self._cbbo_by_symbol: dict[str, list[tuple[int, int, str, int]]] = {}
        self._def_by_day: dict[str, list[str]] = {}
        for r in idx.itertuples(index=False):
            syms = json.loads(r.symbols)
            if r.schema == "cbbo-1m":
                for s in syms:
                    self._cbbo_by_symbol.setdefault(s, []).append(
                        (r.start_ns, r.end_ns, r.path, len(syms))
                    )
            elif r.schema == "definition":
                day = pd.Timestamp(r.start_ns, tz="UTC").date().isoformat()
                self._def_by_day.setdefault(day, []).append(r.path)

        # ── SPX / VIX lookups from the original run's records ─────────────
        dres = pd.read_csv(TEN_YEAR / "daily_results.csv")
        dres["d"] = pd.to_datetime(dres["date"]).dt.date
        self._spx_entry = {
            r.d: float(r.spx_entry) for r in dres.itertuples() if pd.notna(r.spx_entry)
        }
        self._vix_entry = {
            r.d: float(r.vix_entry) for r in dres.itertuples() if pd.notna(r.vix_entry)
        }
        daily = pd.read_csv(TEN_YEAR / "spx_vix_daily.csv", parse_dates=["date"])
        self._spx_close = {
            r.date.date(): float(r.spx_close) for r in daily.itertuples()
        }
        blocked = {
            r.d: r.reason.split(": ", 1)[1]
            for r in dres.itertuples()
            if isinstance(r.reason, str) and r.reason.startswith("macro event filter")
        }
        self.macro = MacroReplay(blocked)
        logger.info(
            f"offline maps: spx_entry={len(self._spx_entry)} days, "
            f"macro_blocked={len(blocked)}, cbbo symbols={len(self._cbbo_by_symbol)}, "
            f"definition days={len(self._def_by_day)}"
        )

    # ── no-op close ───────────────────────────────────────────────────────
    def close(self) -> None:
        pass

    # ── offline SPX/VIX ───────────────────────────────────────────────────
    def _index_value(self, contract, when_et: datetime):
        d = when_et.date()
        if contract.symbol == "VIX":
            return self._vix_entry.get(d)
        if when_et.time() == self.cfg.entry_start_time():
            return self._spx_entry.get(d)
        # EOD check (15:45) — approximated by daily close, documented
        self.stats["eod_close_approx"] += 1
        return self._spx_close.get(d)

    # ── offline Databento ────────────────────────────────────────────────
    def _load_file_df(self, fname: str) -> pd.DataFrame:
        if fname in self._df_lru:
            self._df_lru.move_to_end(fname)
            return self._df_lru[fname]
        df = db.DBNStore.from_file(CACHE_DIR / fname).to_df()
        self._df_lru[fname] = df
        if len(self._df_lru) > 48:
            self._df_lru.popitem(last=False)
        return df

    def _db_get_range_df(self, **kwargs) -> pd.DataFrame:
        # 1) exact query-hash hit (identical to the original mechanism)
        cache_path, _payload = self._cache_path_for_query(kwargs)
        if cache_path and cache_path.exists():
            try:
                self.stats["hit_exact"] += 1
                return db.DBNStore.from_file(cache_path).to_df()
            except Exception:  # noqa: BLE001
                self.stats["hit_exact"] -= 1  # fall through to composition

        schema = kwargs.get("schema")
        start = pd.Timestamp(kwargs.get("start"))
        end = pd.Timestamp(kwargs.get("end"))
        if start.tzinfo is None:
            start = start.tz_localize("UTC")
        if end.tzinfo is None:
            end = end.tz_localize("UTC")
        start_ns, end_ns = start.value, end.value

        if schema == "definition":
            day = start.date().isoformat()
            files = self._def_by_day.get(day, [])
            if files:
                self.stats["hit_composed"] += 1
                return self._load_file_df(files[0])
            self.stats["miss"] += 1
            raise OfflineCacheMiss(f"definitions not cached for {day}")

        if schema == "cbbo-1m":
            syms = kwargs.get("symbols")
            if isinstance(syms, str):
                syms = [syms]
            syms = list(syms)
            # pick, per symbol, a cached file covering the window
            chosen: dict[str, str] = {}
            for s in syms:
                best = None
                for fs, fe, fname, nsym in self._cbbo_by_symbol.get(s, []):
                    if fs <= start_ns and fe >= end_ns:
                        if best is None or nsym < best[1]:
                            best = (fname, nsym)
                if best is None:
                    self.stats["miss"] += 1
                    raise OfflineCacheMiss(
                        f"cbbo-1m not cached for {s} "
                        f"{start.isoformat()}->{end.isoformat()}"
                    )
                chosen[s] = best[0]
            parts = []
            for fname in sorted(set(chosen.values())):
                df = self._load_file_df(fname)
                want = [s for s, f in chosen.items() if f == fname]
                sub = df[df["symbol"].isin(want)]
                sub = sub[(sub.index >= start) & (sub.index < end)]
                parts.append(sub)
            self.stats["hit_composed"] += 1
            out = pd.concat(parts).sort_index() if parts else pd.DataFrame()
            return out

        self.stats["miss"] += 1
        raise OfflineCacheMiss(f"unsupported offline schema {schema}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2013-04-01")
    ap.add_argument("--end", default="2026-04-30")
    ap.add_argument("--max-days", type=int, default=0)
    ap.add_argument("--call-otm-pct", type=float, default=0.010)
    ap.add_argument("--call-width-pt", type=float, default=25.0)
    ap.add_argument("--output-subdir", default="run_default")
    args = ap.parse_args()

    jl.CALL_OTM_PCT = args.call_otm_pct
    jl.CALL_WIDTH_PT = args.call_width_pt

    out_dir = HERE / args.output_subdir
    logger = bd._configure_logger(out_dir)
    cfg = load_config(HERE / "config_jl_replay.yaml")
    stats = {"hit_exact": 0, "hit_composed": 0, "miss": 0, "eod_close_approx": 0}

    bt = OfflineJL(cfg, out_dir, logger, stats)
    daily_df, trades_df, summary = bt.run(
        start_date=bd._parse_date(args.start),
        end_date=bd._parse_date(args.end),
        resume=True,
        max_days=args.max_days,
        sleep_ms=0,
    )
    summary["offline_stats"] = stats
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    logger.info(f"OFFLINE STATS {stats}")
    logger.info(
        f"days={len(daily_df)} trades={len(trades_df)} "
        f"total_pnl={summary.get('total_pnl')} win={summary.get('win_rate')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
