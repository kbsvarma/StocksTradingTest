from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import socket
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import databento as db
import pandas as pd
from ib_insync import IB, Index

from config_loader import BotConfig, load_config
from market_data import MacroCalendar

OCC_SYMBOL_RE = re.compile(
    r"^(?P<root>[A-Z0-9]+)\s+(?P<yymmdd>\d{6})(?P<right>[CP])(?P<strike>\d{8})$"
)


class _MacroLogger:
    def __init__(self, logger: logging.Logger):
        self.logger = logger

    def info(self, msg: str) -> None:
        self.logger.info(msg)

    def warning(self, msg: str) -> None:
        self.logger.warning(msg)

    def error(self, msg: str) -> None:
        self.logger.error(msg)


@dataclass(slots=True)
class Regime:
    name: str
    low: float
    high: float


@dataclass(slots=True)
class Candidate:
    expiry: date
    dte: int
    short_symbol: str
    long_symbol: str
    short_strike: float
    long_strike: float
    otm_pct: float
    target_level: float
    bid: float
    ask: float
    mid: float


@dataclass(slots=True)
class ExitResult:
    reason: str
    exit_time_et: datetime
    exit_price: float
    note: str = ""


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SPX spread strategy historical backtest via Databento + IBKR")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--start-date", default=None, help="YYYY-MM-DD, default=10 years ago from today")
    p.add_argument("--end-date", default=None, help="YYYY-MM-DD, default=today")
    p.add_argument("--output-dir", default="backtests")
    p.add_argument("--resume", action="store_true", help="resume from existing daily CSV in output dir")
    p.add_argument("--max-days", type=int, default=0, help="non-zero to process only N days (debug)")
    p.add_argument("--sleep-ms", type=int, default=0, help="sleep between days (ms) to be gentle on APIs")
    p.add_argument("--cost-preview-days", type=int, default=0, help="use N sample days for Databento cost estimate")
    p.add_argument(
        "--raw-cache-dir",
        default=None,
        help="optional directory to persist raw Databento DBN responses for reuse",
    )
    return p


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _fmt_et(dt_obj: datetime) -> str:
    return dt_obj.strftime("%Y-%m-%d %H:%M:%S %Z")


def _json_safe(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return value


def _occ_parse(raw_symbol: str) -> tuple[Optional[date], Optional[str], Optional[float]]:
    m = OCC_SYMBOL_RE.match(raw_symbol.strip())
    if not m:
        return None, None, None
    yymmdd = m.group("yymmdd")
    right = m.group("right")
    strike = float(int(m.group("strike")) / 1000.0)
    exp = datetime.strptime(yymmdd, "%y%m%d").date()
    return exp, right, strike


def _resolve_regime(cfg: BotConfig, vix: float) -> Optional[Regime]:
    if vix < cfg.vix_min:
        return None
    if vix > cfg.vix_max:
        return None
    if vix <= 20.0:
        low = cfg.otm_pct_low_vix
        return Regime(name="normal", low=low, high=round(low + 0.002, 4))
    high = cfg.otm_pct_high_vix
    return Regime(name="elevated", low=high, high=round(high + 0.005, 4))


class Backtester:
    def __init__(
        self,
        cfg: BotConfig,
        output_dir: Path,
        logger: logging.Logger,
        raw_cache_dir: Optional[Path] = None,
    ):
        self.cfg = cfg
        self.logger = logger
        self.tz = ZoneInfo(cfg.timezone)
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.raw_cache_dir = raw_cache_dir
        if self.raw_cache_dir:
            self.raw_cache_dir.mkdir(parents=True, exist_ok=True)
            self.logger.info(f"Databento raw cache enabled at {self.raw_cache_dir.resolve()}")

        self.db_client = db.Historical()

        self.ib = IB()
        self._connect_ib()
        self.ib.reqMarketDataType(1)
        self.spx_contract = self.ib.qualifyContracts(Index("SPX", cfg.underlying_exchange, cfg.currency))[0]
        self.vix_contract = self.ib.qualifyContracts(Index("VIX", cfg.underlying_exchange, cfg.currency))[0]

        self.macro = MacroCalendar(cfg, _MacroLogger(logger))
        try:
            self.macro.refresh()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(f"macro refresh failed, using cached/manual data: {exc}")

    def _connect_ib(self) -> None:
        base_client_id = self.cfg.client_id + 901
        last_exc: Optional[Exception] = None
        for delta in range(0, 50):
            cid = base_client_id + delta
            try:
                self.ib.connect(self.cfg.host, self.cfg.port, clientId=cid, timeout=20)
                self.logger.info(f"backtest connected to IBKR clientId={cid}")
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                msg = str(exc).lower()
                if "client id is already in use" in msg or "clientid" in msg:
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("unable to connect to IBKR")

    def _cache_path_for_query(self, kwargs: dict) -> tuple[Optional[Path], dict]:
        payload = {
            "dataset": kwargs.get("dataset"),
            "schema": kwargs.get("schema"),
            "stype_in": kwargs.get("stype_in"),
            "stype_out": kwargs.get("stype_out"),
            "start": _json_safe(kwargs.get("start")),
            "end": _json_safe(kwargs.get("end")),
            "symbols": _json_safe(kwargs.get("symbols")),
            "limit": kwargs.get("limit"),
        }
        if not self.raw_cache_dir:
            return None, payload

        payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha1(payload_str.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
        dataset = str(kwargs.get("dataset", "dataset")).replace(".", "_")
        schema = str(kwargs.get("schema", "schema")).replace("-", "_")
        path = self.raw_cache_dir / f"{dataset}__{schema}__{digest}.dbn.zst"
        return path, payload

    def _db_get_range_df(self, **kwargs) -> pd.DataFrame:
        cache_path, payload = self._cache_path_for_query(kwargs)
        if cache_path and cache_path.exists():
            try:
                store = db.DBNStore.from_file(cache_path)
                return store.to_df()
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(f"raw cache read failed for {cache_path.name}; redownloading ({exc})")
                try:
                    cache_path.unlink(missing_ok=True)
                except Exception:  # noqa: BLE001
                    pass

        retries = 4
        sleep_s = 1.5
        for attempt in range(1, retries + 1):
            try:
                query_kwargs = dict(kwargs)
                if cache_path:
                    query_kwargs["path"] = str(cache_path)
                data = self.db_client.timeseries.get_range(**query_kwargs)
                if cache_path:
                    meta_path = cache_path.with_suffix(cache_path.suffix + ".meta.json")
                    if not meta_path.exists():
                        meta = {
                            "created_at_utc": datetime.now(timezone.utc).isoformat(),
                            "query": payload,
                        }
                        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
                return data.to_df()
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                transient = (
                    "timed out" in msg
                    or "timeout" in msg
                    or "504" in msg
                    or isinstance(exc, TimeoutError)
                    or isinstance(exc, socket.timeout)
                )
                if cache_path:
                    try:
                        cache_path.unlink(missing_ok=True)
                    except Exception:  # noqa: BLE001
                        pass
                if attempt >= retries or not transient:
                    raise
                self.logger.warning(f"Databento transient error (attempt {attempt}/{retries}): {exc}")
                time.sleep(sleep_s * attempt)
        return pd.DataFrame()

    def close(self) -> None:
        try:
            if self.ib.isConnected():
                self.ib.disconnect()
        except Exception:  # noqa: BLE001
            pass

    def run(
        self,
        start_date: date,
        end_date: date,
        resume: bool,
        max_days: int,
        sleep_ms: int,
    ) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
        daily_path = self.output_dir / "daily_results.csv"
        trades_path = self.output_dir / "trade_results.csv"

        daily_rows: list[dict] = []
        trade_rows: list[dict] = []
        processed_dates: set[date] = set()

        if resume and daily_path.exists():
            old_daily = pd.read_csv(daily_path)
            if "date" in old_daily.columns:
                for d in old_daily["date"].dropna().astype(str):
                    try:
                        processed_dates.add(_parse_date(d))
                    except Exception:
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
            t0 = time.time()
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
                self.logger.exception(f"{current_day} failed")

            day_record["runtime_sec"] = round(time.time() - t0, 3)
            daily_rows.append(day_record)
            self.logger.info(
                f"{current_day} {day_record['decision']} {day_record['reason']} "
                f"pnl={day_record['total_pnl'] if pd.notna(day_record['total_pnl']) else 'n/a'} "
                f"({day_record['runtime_sec']}s)"
            )

            pd.DataFrame(daily_rows).to_csv(daily_path, index=False)
            if trade_rows:
                pd.DataFrame(trade_rows).to_csv(trades_path, index=False)

            if sleep_ms > 0:
                time.sleep(max(sleep_ms, 0) / 1000.0)

        daily_df = pd.DataFrame(daily_rows)
        trades_df = pd.DataFrame(trade_rows)
        summary = self._build_summary(daily_df, trades_df, start_date, end_date)
        (self.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return daily_df, trades_df, summary

    def estimate_cost(self, days: list[date]) -> dict:
        if not days:
            return {"sample_days": 0}

        sample = sorted(days)[:]
        est = {"sample_days": len(sample), "samples": []}
        for d in sample:
            start_et = datetime.combine(d, self.cfg.entry_start_time(), self.tz)
            end_et = start_et + timedelta(minutes=1)
            try:
                cost = self.db_client.metadata.get_cost(
                    dataset="OPRA.PILLAR",
                    schema="cbbo-1m",
                    stype_in="parent",
                    symbols="SPXW.OPT",
                    start=start_et.astimezone(timezone.utc),
                    end=end_et.astimezone(timezone.utc),
                )
                est["samples"].append({"date": d.isoformat(), "entry_snapshot_cost_usd": float(cost)})
            except Exception as exc:  # noqa: BLE001
                est["samples"].append({"date": d.isoformat(), "entry_snapshot_cost_usd": None, "error": str(exc)})
        valid = [s["entry_snapshot_cost_usd"] for s in est["samples"] if s.get("entry_snapshot_cost_usd") is not None]
        if valid:
            est["avg_entry_snapshot_cost_usd"] = float(sum(valid) / len(valid))
        return est

    def _evaluate_day(self, current_day: date, day_record: dict, trade_rows: list[dict]) -> None:
        if not self.cfg.is_trade_day(current_day):
            day_record["reason"] = "day-of-week filter"
            return

        blocking, ev = self.macro.has_blocking_event(current_day)
        if blocking:
            day_record["reason"] = f"macro event filter: {ev}"
            return

        entry_dt_et = datetime.combine(current_day, self.cfg.entry_start_time(), self.tz)
        spx_entry = self._index_value(self.spx_contract, entry_dt_et)
        vix_entry = self._index_value(self.vix_contract, entry_dt_et)
        if spx_entry is None or vix_entry is None:
            day_record["reason"] = "missing SPX/VIX entry data"
            return

        day_record["spx_entry"] = round(spx_entry, 4)
        day_record["vix_entry"] = round(vix_entry, 4)

        regime = _resolve_regime(self.cfg, vix_entry)
        if regime is None:
            if vix_entry < self.cfg.vix_min:
                day_record["reason"] = f"VIX below minimum ({vix_entry:.2f} < {self.cfg.vix_min})"
            else:
                day_record["reason"] = f"VIX above maximum ({vix_entry:.2f} > {self.cfg.vix_max})"
            return

        definitions = self._definitions_snapshot(current_day)
        if definitions.empty:
            day_record["reason"] = "no option definitions for day"
            return

        candidate_plans = self._build_candidate_plans(current_day, spx_entry, regime, definitions)
        if not candidate_plans:
            day_record["reason"] = "no strike-paired candidates"
            return

        symbols = sorted(
            {plan["short_symbol"] for plan in candidate_plans}.union({plan["long_symbol"] for plan in candidate_plans})
        )
        entry_quotes = self._entry_quotes(current_day, symbols)
        if entry_quotes.empty:
            day_record["reason"] = "no entry quotes for candidates"
            return

        candidate = self._pick_candidate(candidate_plans, entry_quotes)
        if candidate is None:
            day_record["reason"] = "no credit-qualified spread candidate"
            return

        per_spread_risk = max((self.cfg.spread_width - candidate.mid) * 100.0, 1.0)
        by_margin_cap = int(self.cfg.max_margin_dollars() // per_spread_risk)
        contracts = max(0, min(self.cfg.max_contracts, by_margin_cap))
        if contracts <= 0:
            day_record["reason"] = "margin cap allows zero contracts"
            return

        estimated_margin = per_spread_risk * contracts
        if estimated_margin > self.cfg.max_margin_dollars() + 1e-9:
            day_record["reason"] = "estimated margin exceeds max margin"
            return

        stop_price = round(candidate.mid * self.cfg.stop_multiplier, 2)
        profit_target = round(candidate.mid * (1 - self.cfg.profit_target_pct), 2)

        exit_result = self._simulate_exit(
            current_day=current_day,
            short_symbol=candidate.short_symbol,
            long_symbol=candidate.long_symbol,
            entry_dt_et=entry_dt_et,
            stop_price=stop_price,
            profit_target=profit_target,
            short_strike=candidate.short_strike,
        )

        if exit_result.reason == "expiry":
            pnl_per_contract = round(candidate.mid * 100.0, 2)
        else:
            pnl_per_contract = round((candidate.mid - exit_result.exit_price) * 100.0, 2)
        total_pnl = round(pnl_per_contract * contracts, 2)

        day_record.update(
            {
                "decision": "ENTER",
                "reason": f"enter ({regime.name})",
                "expiry": candidate.expiry.isoformat(),
                "dte": candidate.dte,
                "short_strike": candidate.short_strike,
                "long_strike": candidate.long_strike,
                "entry_credit": candidate.mid,
                "contracts": contracts,
                "estimated_margin": round(estimated_margin, 2),
                "exit_time": _fmt_et(exit_result.exit_time_et),
                "exit_price": round(exit_result.exit_price, 2),
                "exit_reason": exit_result.reason,
                "pnl_per_contract": pnl_per_contract,
                "total_pnl": total_pnl,
                "win_loss": "Win" if total_pnl >= 0 else "Loss",
            }
        )

        trade_rows.append(
            {
                "Date": current_day.isoformat(),
                "Entry time": entry_dt_et.strftime("%H:%M:%S"),
                "SPX price at entry": round(spx_entry, 4),
                "VIX at entry": round(vix_entry, 4),
                "Short strike": candidate.short_strike,
                "Long strike": candidate.long_strike,
                "Credit received": candidate.mid,
                "Contracts": contracts,
                "Exit time": exit_result.exit_time_et.strftime("%H:%M:%S"),
                "Exit price": round(exit_result.exit_price, 2),
                "PnL per contract": pnl_per_contract,
                "Total PnL": total_pnl,
                "Win/Loss": "Win" if total_pnl >= 0 else "Loss",
                "Exit reason": exit_result.reason,
                "Notes": exit_result.note,
                "Expiry": candidate.expiry.isoformat(),
                "DTE": candidate.dte,
                "Short symbol": candidate.short_symbol,
                "Long symbol": candidate.long_symbol,
            }
        )

    def _definitions_snapshot(self, current_day: date) -> pd.DataFrame:
        end_day = current_day + timedelta(days=1)
        df = self._db_get_range_df(
            dataset="OPRA.PILLAR",
            schema="definition",
            stype_in="parent",
            symbols="SPXW.OPT",
            start=current_day,
            end=end_day,
        )
        if df.empty:
            return df
        if "raw_symbol" not in df.columns:
            return pd.DataFrame()

        parsed = df["raw_symbol"].astype(str).map(_occ_parse)
        df = df.assign(
            expiry=[p[0] for p in parsed],
            right=[p[1] for p in parsed],
            strike=[p[2] for p in parsed],
        )
        df = df.dropna(subset=["expiry", "right", "strike"])
        df = df[df["right"] == "P"]
        allowed_exp = {current_day + timedelta(days=dte) for dte in self.cfg.dte_candidates}
        df = df[df["expiry"].isin(allowed_exp)]
        if df.empty:
            return df

        snap = df.groupby("raw_symbol").last()[["expiry", "strike"]].reset_index().rename(columns={"raw_symbol": "symbol"})
        return snap

    def _build_candidate_plans(self, current_day: date, spx: float, regime: Regime, definitions: pd.DataFrame) -> list[dict]:
        plans: list[dict] = []
        otm_points = [
            regime.low,
            round((regime.low + regime.high) / 2.0, 4),
            regime.high,
        ]

        for dte in self.cfg.dte_candidates:
            expiry = current_day + timedelta(days=dte)
            day_rows = definitions[definitions["expiry"] == expiry]
            if day_rows.empty:
                continue

            sym_by_strike: dict[float, str] = {}
            for row in day_rows.to_dict(orient="records"):
                strike = float(row["strike"])
                symbol = str(row["symbol"])
                if strike not in sym_by_strike:
                    sym_by_strike[strike] = symbol

            strikes = sorted(sym_by_strike.keys())
            strike_set = set(strikes)

            for otm in otm_points:
                target_level = spx * (1.0 - otm)
                cands = [s for s in strikes if s <= target_level]
                if not cands:
                    continue
                short_base = max(cands)
                base_idx = strikes.index(short_base)

                for offset in range(0, 10):
                    idx = base_idx - offset
                    if idx < 0:
                        break
                    short_strike = strikes[idx]
                    long_strike = round(short_strike - self.cfg.spread_width, 3)
                    if long_strike not in strike_set:
                        continue
                    plans.append(
                        {
                            "expiry": expiry,
                            "dte": dte,
                            "otm_pct": otm,
                            "target_level": target_level,
                            "short_strike": short_strike,
                            "long_strike": long_strike,
                            "short_symbol": sym_by_strike[short_strike],
                            "long_symbol": sym_by_strike[long_strike],
                        }
                    )
        return plans

    def _entry_quotes(self, current_day: date, symbols: list[str]) -> pd.DataFrame:
        if not symbols:
            return pd.DataFrame()

        start_et = datetime.combine(current_day, self.cfg.entry_start_time(), self.tz)
        end_et = start_et + timedelta(minutes=1)
        df = self._db_get_range_df(
            dataset="OPRA.PILLAR",
            schema="cbbo-1m",
            stype_in="raw_symbol",
            symbols=symbols,
            start=start_et.astimezone(timezone.utc),
            end=end_et.astimezone(timezone.utc),
        )
        if df.empty:
            return df
        if "publisher_id" in df.columns:
            df = df[df["publisher_id"] == 30]
        if df.empty:
            return df

        df = df[df["symbol"].isin(symbols)]
        df = df.dropna(subset=["bid_px_00", "ask_px_00"])
        df = df[(df["bid_px_00"] > 0) & (df["ask_px_00"] > 0)]
        if df.empty:
            return df

        snap = (
            df.sort_index()
            .groupby("symbol")
            .last()[["bid_px_00", "ask_px_00"]]
            .rename(columns={"bid_px_00": "bid", "ask_px_00": "ask"})
            .reset_index()
        )
        return snap

    def _pick_candidate(self, plans: list[dict], quotes: pd.DataFrame) -> Optional[Candidate]:
        if not plans or quotes.empty:
            return None

        qmap = {str(r["symbol"]): r for r in quotes.to_dict(orient="records")}
        best: Optional[Candidate] = None
        best_score = math.inf

        for plan in plans:
            short_q = qmap.get(plan["short_symbol"])
            long_q = qmap.get(plan["long_symbol"])
            if not short_q or not long_q:
                continue

            bid = float(short_q["bid"]) - float(long_q["ask"])
            ask = float(short_q["ask"]) - float(long_q["bid"])
            if bid <= 0 or ask <= 0:
                continue
            mid = round((bid + ask) / 2.0, 2)
            if mid < self.cfg.min_credit:
                continue

            score = abs(self.cfg.target_credit - mid)
            if score < best_score:
                best_score = score
                best = Candidate(
                    expiry=plan["expiry"],
                    dte=int(plan["dte"]),
                    short_symbol=plan["short_symbol"],
                    long_symbol=plan["long_symbol"],
                    short_strike=float(plan["short_strike"]),
                    long_strike=float(plan["long_strike"]),
                    otm_pct=float(plan["otm_pct"]),
                    target_level=float(plan["target_level"]),
                    bid=round(bid, 2),
                    ask=round(ask, 2),
                    mid=mid,
                )

        return best

    def _simulate_exit(
        self,
        current_day: date,
        short_symbol: str,
        long_symbol: str,
        entry_dt_et: datetime,
        stop_price: float,
        profit_target: float,
        short_strike: float,
    ) -> ExitResult:
        friday_force = datetime.combine(current_day, self.cfg.friday_forced_close_time(), self.tz)
        eod_check = datetime.combine(current_day, self.cfg.eod_check_time(), self.tz)
        market_end = datetime.combine(current_day, dt_time(hour=16, minute=0), self.tz)

        quote_frame = self._load_pair_quotes(short_symbol, long_symbol, entry_dt_et, market_end)
        if quote_frame.empty:
            return ExitResult(reason="data_unavailable", exit_time_et=entry_dt_et, exit_price=0.0, note="no quote bars")

        last_mark: Optional[float] = None
        for ts_et, row in quote_frame.iterrows():
            mark = self._mark_from_row(row)
            if mark is not None:
                last_mark = mark

                if mark >= stop_price:
                    return ExitResult(reason="stop_loss", exit_time_et=ts_et, exit_price=mark, note="mark>=stop")

                if mark <= profit_target:
                    return ExitResult(reason="profit_target", exit_time_et=ts_et, exit_price=profit_target, note="mark<=target")

            if current_day.weekday() == 4 and ts_et >= friday_force:
                px = mark if mark is not None else (last_mark if last_mark is not None else 0.0)
                return ExitResult(reason="friday_force_close", exit_time_et=ts_et, exit_price=px, note="friday hard close")

            if ts_et >= eod_check:
                spx_eod = self._index_value(self.spx_contract, eod_check)
                if spx_eod is None:
                    px = mark if mark is not None else (last_mark if last_mark is not None else 0.0)
                    return ExitResult(reason="eod_force_close", exit_time_et=ts_et, exit_price=px, note="missing SPX@eod")

                if (spx_eod - short_strike) <= 50.0:
                    px = mark if mark is not None else (last_mark if last_mark is not None else 0.0)
                    return ExitResult(reason="eod_force_close", exit_time_et=ts_et, exit_price=px, note="within 50pt")

                if ts_et >= market_end:
                    return ExitResult(reason="expiry", exit_time_et=market_end, exit_price=0.0, note="distance safe")

        if last_mark is not None:
            return ExitResult(reason="eod_force_close", exit_time_et=market_end, exit_price=last_mark, note="fell through")
        return ExitResult(reason="data_unavailable", exit_time_et=market_end, exit_price=0.0, note="no mark")

    def _load_pair_quotes(
        self,
        short_symbol: str,
        long_symbol: str,
        start_et: datetime,
        end_et: datetime,
    ) -> pd.DataFrame:
        df = self._db_get_range_df(
            dataset="OPRA.PILLAR",
            schema="cbbo-1m",
            stype_in="raw_symbol",
            symbols=[short_symbol, long_symbol],
            start=start_et.astimezone(timezone.utc),
            end=end_et.astimezone(timezone.utc),
        )
        if df.empty:
            return df
        if "publisher_id" in df.columns:
            df = df[df["publisher_id"] == 30]
        if df.empty:
            return df

        df = df[df["symbol"].isin([short_symbol, long_symbol])]
        df = df.sort_index()
        if df.empty:
            return df

        ts_et = pd.to_datetime(df.index, utc=True).tz_convert(self.tz)
        df = df.assign(ts_et=ts_et)

        wide = (
            df.groupby(["ts_et", "symbol"])[["bid_px_00", "ask_px_00"]]
            .last()
            .unstack("symbol")
            .sort_index()
        )
        idx = pd.date_range(start=start_et, end=end_et, freq="1min", tz=self.tz)
        wide = wide.reindex(idx).ffill()

        out = pd.DataFrame(index=wide.index)
        out["short_bid"] = wide[("bid_px_00", short_symbol)]
        out["short_ask"] = wide[("ask_px_00", short_symbol)]
        out["long_bid"] = wide[("bid_px_00", long_symbol)]
        out["long_ask"] = wide[("ask_px_00", long_symbol)]
        return out

    @staticmethod
    def _mark_from_row(row: pd.Series) -> Optional[float]:
        sb = float(row.get("short_bid") or 0.0)
        sa = float(row.get("short_ask") or 0.0)
        lb = float(row.get("long_bid") or 0.0)
        la = float(row.get("long_ask") or 0.0)
        if sb <= 0 or sa <= 0 or lb <= 0 or la <= 0:
            return None
        bid = sb - la
        ask = sa - lb
        if bid <= 0 or ask <= 0:
            return None
        return round((bid + ask) / 2.0, 2)

    def _index_value(self, contract, when_et: datetime) -> Optional[float]:
        end_utc = when_et.astimezone(timezone.utc).strftime("%Y%m%d %H:%M:%S")
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime=end_utc,
            durationStr="7200 S",
            barSizeSetting="1 min",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=2,
        )
        if not bars:
            return None

        best = None
        for bar in bars:
            dt_utc = bar.date
            if not isinstance(dt_utc, datetime):
                continue
            dt_et = dt_utc.astimezone(self.tz)
            if dt_et.date() != when_et.date():
                continue
            if dt_et > when_et:
                continue
            best = bar.close
        return float(best) if best is not None else None

    @staticmethod
    def _build_summary(daily_df: pd.DataFrame, trades_df: pd.DataFrame, start_date: date, end_date: date) -> dict:
        summary = {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "days_evaluated": int(len(daily_df)),
            "trades_taken": int((daily_df.get("decision") == "ENTER").sum()) if not daily_df.empty else 0,
            "skips": int((daily_df.get("decision") != "ENTER").sum()) if not daily_df.empty else 0,
            "total_pnl": 0.0,
            "avg_trade_pnl": 0.0,
            "win_rate": 0.0,
            "max_drawdown": 0.0,
            "skip_reasons": {},
        }
        if daily_df.empty:
            return summary

        if "reason" in daily_df.columns:
            skips = daily_df[daily_df["decision"] != "ENTER"]["reason"].fillna("unknown")
            summary["skip_reasons"] = skips.value_counts().to_dict()

        if trades_df.empty:
            return summary

        pnl = pd.to_numeric(trades_df["Total PnL"], errors="coerce").fillna(0.0)
        summary["total_pnl"] = round(float(pnl.sum()), 2)
        summary["avg_trade_pnl"] = round(float(pnl.mean()), 2)
        wins = int((pnl >= 0).sum())
        losses = int((pnl < 0).sum())
        summary["win_rate"] = round((wins / (wins + losses)) if (wins + losses) else 0.0, 4)

        eq = pnl.cumsum()
        peak = eq.cummax()
        dd = eq - peak
        summary["max_drawdown"] = round(float(dd.min()) if not dd.empty else 0.0, 2)
        return summary


def _configure_logger(output_dir: Path) -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("spx_backtest")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    fh = logging.FileHandler(output_dir / "backtest.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


def main() -> int:
    args = _build_parser().parse_args()
    if not os.getenv("DATABENTO_API_KEY"):
        raise SystemExit("DATABENTO_API_KEY is not set")

    cfg = load_config(args.config)
    output_dir = Path(args.output_dir)
    logger = _configure_logger(output_dir)

    today_et = datetime.now(ZoneInfo(cfg.timezone)).date()
    start_date = _parse_date(args.start_date) if args.start_date else (today_et - timedelta(days=3650))
    end_date = _parse_date(args.end_date) if args.end_date else today_et
    if end_date < start_date:
        raise SystemExit("end-date must be >= start-date")

    logger.info(
        f"starting backtest window {start_date.isoformat()} -> {end_date.isoformat()} "
        f"mode={'resume' if args.resume else 'fresh'}"
    )

    raw_cache_dir = Path(args.raw_cache_dir) if args.raw_cache_dir else None
    bt = Backtester(cfg, output_dir, logger, raw_cache_dir=raw_cache_dir)
    try:
        all_days = [d.date() for d in pd.date_range(start=start_date, end=end_date, freq="D")]
        trade_days = [d for d in all_days if cfg.is_trade_day(d)]
        if args.cost_preview_days > 0 and trade_days:
            est = bt.estimate_cost(trade_days[: args.cost_preview_days])
            (output_dir / "cost_preview.json").write_text(json.dumps(est, indent=2), encoding="utf-8")
            logger.info(f"wrote cost preview for {est.get('sample_days', 0)} sample day(s)")

        daily_df, trades_df, summary = bt.run(
            start_date=start_date,
            end_date=end_date,
            resume=args.resume,
            max_days=args.max_days,
            sleep_ms=args.sleep_ms,
        )
    finally:
        bt.close()

    logger.info(f"finished days={len(daily_df)} trades={len(trades_df)} total_pnl={summary.get('total_pnl')}")
    logger.info(f"summary file: {(output_dir / 'summary.json').resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
