from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import databento as db
import pandas as pd
import yaml

OCC_SYMBOL_RE = re.compile(
    r"^(?P<root>[A-Z0-9]+)\s+(?P<yymmdd>\d{6})(?P<right>[CP])(?P<strike>\d{8})$"
)


@dataclass(slots=True)
class Cfg:
    run_days: list[int]
    dte_candidates: list[int]
    target_credit: float
    min_credit: float
    stop_multiplier: float
    profit_target_pct: float
    otm_pct_low_vix: float
    otm_pct_high_vix: float
    vix_max: float
    spread_width: int
    bwb_narrow_wing_width: int
    bwb_wide_wing_width: int
    condor_wing_width: int
    iron_fly_wing_width: int
    timezone: str


@dataclass(slots=True)
class Leg:
    symbol: str
    right: str
    strike: float
    direction: str  # SHORT/LONG
    quantity: int = 1


@dataclass(slots=True)
class Candidate:
    strategy: str
    expiry: date
    dte: int
    otm_pct: float
    target_level: float
    legs: list[Leg]
    short_put_strike: float = 0.0
    long_put_strike: float = 0.0
    short_call_strike: float = 0.0
    long_call_strike: float = 0.0
    credit_bid: float = 0.0
    credit_ask: float = 0.0
    credit_mid: float = 0.0
    max_loss_per_contract: float = 0.0


@dataclass(slots=True)
class SimResult:
    strategy: str
    trade_date: date
    decision: str
    reason: str
    contracts: int = 0
    entry_credit: float = 0.0
    exit_price: float = 0.0
    pnl_per_contract: float = 0.0
    total_pnl: float = 0.0
    exit_reason: str = ""
    expiry: str = ""
    dte: int = 0
    short_put_strike: float = 0.0
    long_put_strike: float = 0.0
    short_call_strike: float = 0.0
    long_call_strike: float = 0.0


class DBCacheClient:
    def __init__(self, raw_cache_dir: Path, api_key: str):
        self.raw_cache_dir = raw_cache_dir
        self.raw_cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = db.Historical(key=api_key)
        self._window_cache: dict[str, pd.DataFrame] = {}

    @staticmethod
    def _json_safe(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (list, tuple, set)):
            return [DBCacheClient._json_safe(v) for v in value]
        if isinstance(value, dict):
            return {str(k): DBCacheClient._json_safe(v) for k, v in value.items()}
        return value

    def _cache_path_for_query(self, kwargs: dict) -> tuple[Path, dict]:
        payload = {
            "dataset": kwargs.get("dataset"),
            "schema": kwargs.get("schema"),
            "stype_in": kwargs.get("stype_in"),
            "stype_out": kwargs.get("stype_out"),
            "start": self._json_safe(kwargs.get("start")),
            "end": self._json_safe(kwargs.get("end")),
            "symbols": self._json_safe(kwargs.get("symbols")),
            "limit": kwargs.get("limit"),
        }
        payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha1(payload_str.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
        dataset = str(kwargs.get("dataset", "dataset")).replace(".", "_")
        schema = str(kwargs.get("schema", "schema")).replace("-", "_")
        path = self.raw_cache_dir / f"{dataset}__{schema}__{digest}.dbn.zst"
        return path, payload

    def get_df(self, **kwargs) -> pd.DataFrame:
        path, payload = self._cache_path_for_query(kwargs)
        if path.exists():
            try:
                return db.DBNStore.from_file(path).to_df()
            except Exception:
                path.unlink(missing_ok=True)

        q = dict(kwargs)
        q["path"] = str(path)
        store = self.client.timeseries.get_range(**q)
        meta_path = path.with_suffix(path.suffix + ".meta.json")
        if not meta_path.exists():
            meta = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "query": payload}
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return store.to_df()

    def get_window(
        self,
        symbols: list[str],
        start_utc: datetime,
        end_utc: datetime,
        *,
        schema: str = "cbbo-1m",
        stype_in: str = "raw_symbol",
    ) -> pd.DataFrame:
        key = json.dumps(
            {
                "symbols": symbols,
                "start": start_utc.isoformat(),
                "end": end_utc.isoformat(),
                "schema": schema,
                "stype_in": stype_in,
            },
            sort_keys=True,
        )
        cached = self._window_cache.get(key)
        if cached is not None:
            return cached

        df = self.get_df(
            dataset="OPRA.PILLAR",
            schema=schema,
            stype_in=stype_in,
            symbols=symbols,
            start=start_utc,
            end=end_utc,
        )
        self._window_cache[key] = df
        return df


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate multi-strategy snapshot report on cached Databento dataset.")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--daily-csv", default="backtests/ten_year/daily_results.csv")
    p.add_argument("--output-dir", default="backtests/strategy_snapshot")
    p.add_argument("--raw-cache-dir", default="backtests/ten_year/raw_dbn_cache")
    p.add_argument("--start-date", default=None, help="YYYY-MM-DD")
    p.add_argument("--end-date", default=None, help="YYYY-MM-DD")
    p.add_argument("--capital", type=float, default=100000.0)
    p.add_argument("--max-margin-pct", type=float, default=0.50)
    p.add_argument("--max-contracts", type=int, default=10)
    p.add_argument("--status-every", type=int, default=50)
    return p.parse_args()


def _load_cfg(path: Path) -> Cfg:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Cfg(
        run_days=list(raw.get("run_days", [0, 2, 4])),
        dte_candidates=list(raw.get("dte_candidates", [0, 2])),
        target_credit=float(raw.get("target_credit", 2.0)),
        min_credit=float(raw.get("min_credit", 1.0)),
        stop_multiplier=float(raw.get("stop_multiplier", 2.0)),
        profit_target_pct=float(raw.get("profit_target_pct", 0.50)),
        otm_pct_low_vix=float(raw.get("otm_pct_low_vix", 0.018)),
        otm_pct_high_vix=float(raw.get("otm_pct_high_vix", 0.025)),
        vix_max=float(raw.get("vix_max", 30.0)),
        spread_width=int(raw.get("spread_width", 25)),
        bwb_narrow_wing_width=int(raw.get("bwb_narrow_wing_width", 25)),
        bwb_wide_wing_width=int(raw.get("bwb_wide_wing_width", 50)),
        condor_wing_width=int(raw.get("condor_wing_width", 25)),
        iron_fly_wing_width=int(raw.get("iron_fly_wing_width", 25)),
        timezone=str(raw.get("timezone", "America/New_York")),
    )


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _occ_parse(raw_symbol: str) -> tuple[Optional[date], Optional[str], Optional[float]]:
    m = OCC_SYMBOL_RE.match(raw_symbol.strip())
    if not m:
        return None, None, None
    yymmdd = m.group("yymmdd")
    right = m.group("right")
    strike = float(int(m.group("strike")) / 1000.0)
    exp = datetime.strptime(yymmdd, "%y%m%d").date()
    return exp, right, strike


def _resolve_regime(cfg: Cfg, vix: float) -> Optional[tuple[float, float]]:
    if vix > cfg.vix_max:
        return None
    if vix <= 20.0:
        low = cfg.otm_pct_low_vix
        return low, round(low + 0.002, 4)
    high = cfg.otm_pct_high_vix
    return high, round(high + 0.005, 4)


def _nearest_at_or_below(strikes: list[float], target: float) -> Optional[float]:
    vals = [s for s in strikes if s <= target]
    return max(vals) if vals else None


def _nearest_at_or_above(strikes: list[float], target: float) -> Optional[float]:
    vals = [s for s in strikes if s >= target]
    return min(vals) if vals else None


def _nearest(strikes: list[float], target: float) -> Optional[float]:
    if not strikes:
        return None
    return min(strikes, key=lambda s: abs(s - target))


def _ladder_at_or_below(strikes: list[float], start: float, depth: int) -> list[float]:
    try:
        idx = strikes.index(start)
    except ValueError:
        return []
    return [strikes[i] for i in range(idx, max(-1, idx - depth), -1)]


def _ladder_at_or_above(strikes: list[float], start: float, depth: int) -> list[float]:
    try:
        idx = strikes.index(start)
    except ValueError:
        return []
    upper = min(len(strikes), idx + depth)
    return [strikes[i] for i in range(idx, upper)]


def _estimate_max_loss_dollars(legs: list[Leg], credit: float) -> float:
    unique_strikes = sorted({float(leg.strike) for leg in legs})
    if not unique_strikes:
        return 0.0
    scenarios = [0.0, *unique_strikes, unique_strikes[-1] + 500.0]
    worst_loss_points = 0.0
    for spot in scenarios:
        pnl_points = credit
        for leg in legs:
            intrinsic = max(leg.strike - spot, 0.0) if leg.right == "P" else max(spot - leg.strike, 0.0)
            qty = max(int(leg.quantity), 1)
            if leg.direction == "SHORT":
                pnl_points -= intrinsic * qty
            else:
                pnl_points += intrinsic * qty
        worst_loss_points = max(worst_loss_points, max(0.0, -pnl_points))
    return round(worst_loss_points * 100.0, 2)


def _entry_quote(legs: list[Leg], qmap: dict[str, dict[str, float]]) -> Optional[tuple[float, float, float]]:
    short_bid_total = 0.0
    short_ask_total = 0.0
    long_bid_total = 0.0
    long_ask_total = 0.0

    for leg in legs:
        q = qmap.get(leg.symbol)
        if not q:
            return None
        bid = float(q.get("bid") or 0.0)
        ask = float(q.get("ask") or 0.0)
        if bid <= 0 or ask <= 0:
            return None
        qty = max(int(leg.quantity), 1)
        if leg.direction == "SHORT":
            short_bid_total += bid * qty
            short_ask_total += ask * qty
        else:
            long_bid_total += bid * qty
            long_ask_total += ask * qty

    credit_bid = short_bid_total - long_ask_total
    credit_ask = short_ask_total - long_bid_total
    if credit_bid <= 0 or credit_ask <= 0:
        return None
    mid = round((credit_bid + credit_ask) / 2.0, 2)
    return round(credit_bid, 2), round(credit_ask, 2), mid


def _mark_from_row(row: pd.Series, legs: list[Leg]) -> Optional[float]:
    debit_bid = 0.0
    debit_ask = 0.0
    for leg in legs:
        bid = float(row.get(f"{leg.symbol}__bid", 0.0) or 0.0)
        ask = float(row.get(f"{leg.symbol}__ask", 0.0) or 0.0)
        if bid <= 0 or ask <= 0:
            return None
        qty = max(int(leg.quantity), 1)
        if leg.direction == "SHORT":
            debit_bid += bid * qty
            debit_ask += ask * qty
        else:
            debit_bid -= ask * qty
            debit_ask -= bid * qty
    return round(max((debit_bid + debit_ask) / 2.0, 0.0), 2)


def _normalize_quotes(
    df: pd.DataFrame,
    symbols: list[str],
    idx: pd.DatetimeIndex,
    tz: ZoneInfo,
) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(index=idx)
    if "publisher_id" in df.columns:
        df = df[df["publisher_id"] == 30]
    if df.empty:
        return pd.DataFrame(index=idx)
    df = df[df["symbol"].isin(symbols)]
    if df.empty:
        return pd.DataFrame(index=idx)
    df = df.dropna(subset=["bid_px_00", "ask_px_00"])
    df = df[(df["bid_px_00"] > 0) & (df["ask_px_00"] > 0)]
    if df.empty:
        return pd.DataFrame(index=idx)
    ts_et = pd.to_datetime(df.index, utc=True).tz_convert(tz)
    df = df.assign(ts_et=ts_et)
    wide = (
        df.groupby(["ts_et", "symbol"])[["bid_px_00", "ask_px_00"]]
        .last()
        .unstack("symbol")
        .sort_index()
    )
    wide = wide.reindex(idx).ffill()
    out = pd.DataFrame(index=idx)
    for sym in symbols:
        if ("bid_px_00", sym) in wide.columns:
            out[f"{sym}__bid"] = wide[("bid_px_00", sym)]
        else:
            out[f"{sym}__bid"] = 0.0
        if ("ask_px_00", sym) in wide.columns:
            out[f"{sym}__ask"] = wide[("ask_px_00", sym)]
        else:
            out[f"{sym}__ask"] = 0.0
    return out


def _entry_qmap(df: pd.DataFrame, symbols: list[str]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    if df.empty:
        return out
    if "publisher_id" in df.columns:
        df = df[df["publisher_id"] == 30]
    if df.empty:
        return out
    df = df[df["symbol"].isin(symbols)]
    df = df.dropna(subset=["bid_px_00", "ask_px_00"])
    df = df[(df["bid_px_00"] > 0) & (df["ask_px_00"] > 0)]
    if df.empty:
        return out
    snap = (
        df.sort_index()
        .groupby("symbol")
        .last()[["bid_px_00", "ask_px_00"]]
        .rename(columns={"bid_px_00": "bid", "ask_px_00": "ask"})
        .reset_index()
    )
    for r in snap.to_dict(orient="records"):
        out[str(r["symbol"])] = {"bid": float(r["bid"]), "ask": float(r["ask"])}
    return out


def _build_candidates(
    cfg: Cfg,
    current_day: date,
    spx: float,
    regime: tuple[float, float],
    definitions: pd.DataFrame,
) -> list[Candidate]:
    otm_low, otm_high = regime
    otm_points = [otm_low, round((otm_low + otm_high) / 2.0, 4), otm_high]
    out: list[Candidate] = []

    for dte in cfg.dte_candidates:
        expiry = current_day + timedelta(days=dte)
        day_rows = definitions[definitions["expiry"] == expiry]
        if day_rows.empty:
            continue

        put_by_strike: dict[float, str] = {}
        call_by_strike: dict[float, str] = {}
        for row in day_rows.to_dict(orient="records"):
            strike = float(row["strike"])
            symbol = str(row["symbol"])
            right = str(row["right"])
            if right == "P" and strike not in put_by_strike:
                put_by_strike[strike] = symbol
            if right == "C" and strike not in call_by_strike:
                call_by_strike[strike] = symbol

        put_strikes = sorted(put_by_strike.keys())
        call_strikes = sorted(call_by_strike.keys())
        if not put_strikes:
            continue

        for otm in otm_points:
            put_target = spx * (1.0 - otm)
            short_put_base = _nearest_at_or_below(put_strikes, put_target)
            if short_put_base is None:
                continue
            put_try = _ladder_at_or_below(put_strikes, short_put_base, depth=4)

            for short_put in put_try:
                long_put = _nearest_at_or_below(put_strikes, short_put - float(cfg.spread_width))
                if long_put is not None and long_put < short_put:
                    out.append(
                        Candidate(
                            strategy="BULL_PUT_SPREAD",
                            expiry=expiry,
                            dte=dte,
                            otm_pct=otm,
                            target_level=put_target,
                            short_put_strike=short_put,
                            long_put_strike=long_put,
                            legs=[
                                Leg(put_by_strike[short_put], "P", short_put, "SHORT", 1),
                                Leg(put_by_strike[long_put], "P", long_put, "LONG", 1),
                            ],
                        )
                    )

                upper = _nearest_at_or_above(put_strikes, short_put + float(cfg.bwb_narrow_wing_width))
                lower = _nearest_at_or_below(put_strikes, short_put - float(cfg.bwb_wide_wing_width))
                if upper is not None and lower is not None and upper > short_put > lower:
                    out.append(
                        Candidate(
                            strategy="PUT_BWB",
                            expiry=expiry,
                            dte=dte,
                            otm_pct=otm,
                            target_level=put_target,
                            short_put_strike=short_put,
                            long_put_strike=lower,
                            legs=[
                                Leg(put_by_strike[upper], "P", upper, "LONG", 1),
                                Leg(put_by_strike[short_put], "P", short_put, "SHORT", 2),
                                Leg(put_by_strike[lower], "P", lower, "LONG", 1),
                            ],
                        )
                    )

            if not call_strikes:
                continue
            call_target = spx * (1.0 + otm)
            short_call_base = _nearest_at_or_above(call_strikes, call_target)
            if short_call_base is None:
                continue
            call_try = _ladder_at_or_above(call_strikes, short_call_base, depth=4)
            max_offsets = min(len(put_try), len(call_try), 4)
            for i in range(max_offsets):
                short_put = put_try[i]
                short_call = call_try[i]
                if short_call <= short_put:
                    continue
                put_wing = _nearest_at_or_below(put_strikes, short_put - float(cfg.condor_wing_width))
                call_wing = _nearest_at_or_above(call_strikes, short_call + float(cfg.condor_wing_width))
                if put_wing is None or call_wing is None:
                    continue
                if not (put_wing < short_put < short_call < call_wing):
                    continue
                out.append(
                    Candidate(
                        strategy="IRON_CONDOR",
                        expiry=expiry,
                        dte=dte,
                        otm_pct=otm,
                        target_level=put_target,
                        short_put_strike=short_put,
                        long_put_strike=put_wing,
                        short_call_strike=short_call,
                        long_call_strike=call_wing,
                        legs=[
                            Leg(put_by_strike[short_put], "P", short_put, "SHORT", 1),
                            Leg(put_by_strike[put_wing], "P", put_wing, "LONG", 1),
                            Leg(call_by_strike[short_call], "C", short_call, "SHORT", 1),
                            Leg(call_by_strike[call_wing], "C", call_wing, "LONG", 1),
                        ],
                    )
                )

        if call_strikes:
            center_put = _nearest(put_strikes, spx)
            center_call = _nearest(call_strikes, spx)
            center = None
            if center_put is not None and center_call is not None:
                center = center_put if abs(center_put - spx) <= abs(center_call - spx) else center_call
            elif center_put is not None:
                center = center_put
            elif center_call is not None:
                center = center_call
            if center is not None and center in put_by_strike and center in call_by_strike:
                lower = _nearest_at_or_below(put_strikes, center - float(cfg.iron_fly_wing_width))
                upper = _nearest_at_or_above(call_strikes, center + float(cfg.iron_fly_wing_width))
                if lower is not None and upper is not None and lower < center < upper:
                    out.append(
                        Candidate(
                            strategy="IRON_FLY",
                            expiry=expiry,
                            dte=dte,
                            otm_pct=abs(spx - center) / max(spx, 1.0),
                            target_level=center,
                            short_put_strike=center,
                            long_put_strike=lower,
                            short_call_strike=center,
                            long_call_strike=upper,
                            legs=[
                                Leg(put_by_strike[center], "P", center, "SHORT", 1),
                                Leg(call_by_strike[center], "C", center, "SHORT", 1),
                                Leg(put_by_strike[lower], "P", lower, "LONG", 1),
                                Leg(call_by_strike[upper], "C", upper, "LONG", 1),
                            ],
                        )
                    )

    return out


def _select_candidates_by_strategy(candidates: list[Candidate], cfg: Cfg) -> dict[str, Candidate]:
    best: dict[str, tuple[float, Candidate]] = {}
    for cand in candidates:
        if cand.credit_mid < cfg.min_credit:
            continue
        score = abs(cfg.target_credit - cand.credit_mid)
        prev = best.get(cand.strategy)
        if prev is None or score < prev[0]:
            best[cand.strategy] = (score, cand)
    return {k: v[1] for k, v in best.items()}


def _simulate_standard(
    candidate: Candidate,
    contracts: int,
    quotes: pd.DataFrame,
    now_day: date,
    cfg: Cfg,
) -> SimResult:
    stop_price = round(candidate.credit_mid * cfg.stop_multiplier, 2)
    profit_target = round(candidate.credit_mid * (1.0 - cfg.profit_target_pct), 2)

    last_mark: Optional[float] = None
    exit_reason = "expiry"
    exit_price = 0.0

    for ts, row in quotes.iterrows():
        mark = _mark_from_row(row, candidate.legs)
        if mark is not None:
            last_mark = mark
            if mark >= stop_price:
                exit_reason = "stop_loss"
                exit_price = mark
                break
            if mark <= profit_target:
                exit_reason = "profit_target"
                exit_price = profit_target
                break
        if now_day.weekday() == 4 and ts.time() >= dt_time(15, 30):
            exit_reason = "friday_force_close"
            exit_price = mark if mark is not None else (last_mark if last_mark is not None else 0.0)
            break
        if ts.time() >= dt_time(15, 45):
            exit_reason = "eod_force_close"
            exit_price = mark if mark is not None else (last_mark if last_mark is not None else float(cfg.spread_width))
            break

    if exit_reason == "expiry":
        pnl_per_contract = round(candidate.credit_mid * 100.0, 2)
    else:
        pnl_per_contract = round((candidate.credit_mid - exit_price) * 100.0, 2)
    total_pnl = round(pnl_per_contract * contracts, 2)

    return SimResult(
        strategy=candidate.strategy,
        trade_date=now_day,
        decision="ENTER",
        reason="enter",
        contracts=contracts,
        entry_credit=candidate.credit_mid,
        exit_price=round(exit_price, 2),
        pnl_per_contract=pnl_per_contract,
        total_pnl=total_pnl,
        exit_reason=exit_reason,
        expiry=candidate.expiry.isoformat(),
        dte=candidate.dte,
        short_put_strike=candidate.short_put_strike,
        long_put_strike=candidate.long_put_strike,
        short_call_strike=candidate.short_call_strike,
        long_call_strike=candidate.long_call_strike,
    )


def _simulate_harvester(
    candidate: Candidate,
    quotes: pd.DataFrame,
    now_day: date,
) -> SimResult:
    # Harvester profile rules (separate design track).
    daily_target = 100.0
    daily_stop = 300.0
    max_contracts_h = 8
    pt1 = 0.35
    max_tail_loss = 6000.0

    pnl_pt1_per_contract = candidate.credit_mid * pt1 * 100.0
    if pnl_pt1_per_contract <= 0:
        return SimResult(strategy="HARVESTER_100_V3", trade_date=now_day, decision="SKIP", reason="invalid credit")

    qty_target = math.ceil(daily_target / pnl_pt1_per_contract)
    qty_stop_cap = math.floor(daily_stop / max(candidate.credit_mid * 100.0, 1.0))
    width_points = max(abs(candidate.short_put_strike - candidate.long_put_strike), 0.01)
    per_contract_tail = max((width_points - candidate.credit_mid) * 100.0, 1.0)
    qty_tail_cap = math.floor(max_tail_loss / per_contract_tail)
    contracts = max(0, min(max_contracts_h, qty_target, qty_stop_cap, qty_tail_cap))
    if contracts <= 0:
        return SimResult(strategy="HARVESTER_100_V3", trade_date=now_day, decision="SKIP", reason="sizing zero")

    stop_floor = -300.0
    thresholds = [(50.0, -100.0), (100.0, 0.0), (200.0, 100.0)]
    realized = 0.0
    open_qty = contracts
    scaled = False
    exit_reason = "noon_close"
    exit_price = 0.0

    for ts, row in quotes.iterrows():
        if ts.time() > dt_time(12, 0):
            break
        mark = _mark_from_row(row, candidate.legs)
        if mark is None:
            continue
        exit_price = mark
        unreal = (candidate.credit_mid - mark) * 100.0 * open_qty
        total_pnl = realized + unreal

        for lvl, floor in thresholds:
            if total_pnl >= lvl:
                stop_floor = max(stop_floor, floor)

        if (not scaled) and total_pnl >= daily_target:
            if open_qty >= 2:
                close_qty = math.ceil(contracts * 0.5)
                close_qty = min(close_qty, open_qty)
                realized += (candidate.credit_mid - mark) * 100.0 * close_qty
                open_qty -= close_qty
                scaled = True
                stop_floor = max(stop_floor, 0.0)
                if open_qty <= 0:
                    exit_reason = "target_scale_close"
                    break
            else:
                realized += (candidate.credit_mid - mark) * 100.0 * open_qty
                open_qty = 0
                exit_reason = "target_hit_single"
                break

        live_total = realized + (candidate.credit_mid - mark) * 100.0 * open_qty
        if live_total <= stop_floor:
            realized += (candidate.credit_mid - mark) * 100.0 * open_qty
            open_qty = 0
            exit_reason = "ratchet_stop"
            break

    if open_qty > 0:
        # Noon hard close for remaining quantity.
        if exit_price <= 0:
            # If we never got a valid mark, close at full width pessimistically.
            exit_price = float(abs(candidate.short_put_strike - candidate.long_put_strike))
        realized += (candidate.credit_mid - exit_price) * 100.0 * open_qty
        open_qty = 0
        if exit_reason == "noon_close":
            exit_reason = "time_noon_close"

    total_pnl = round(realized, 2)
    pnl_per_contract = round(total_pnl / max(contracts, 1), 2)
    return SimResult(
        strategy="HARVESTER_100_V3",
        trade_date=now_day,
        decision="ENTER",
        reason="enter",
        contracts=contracts,
        entry_credit=candidate.credit_mid,
        exit_price=round(exit_price, 2),
        pnl_per_contract=pnl_per_contract,
        total_pnl=total_pnl,
        exit_reason=exit_reason,
        expiry=candidate.expiry.isoformat(),
        dte=candidate.dte,
        short_put_strike=candidate.short_put_strike,
        long_put_strike=candidate.long_put_strike,
    )


def _summarize(records: list[SimResult]) -> pd.DataFrame:
    rows: list[dict] = []
    df = pd.DataFrame([asdict(r) for r in records])
    for strategy in ["BULL_PUT_SPREAD", "PUT_BWB", "IRON_CONDOR", "IRON_FLY", "HARVESTER_100_V3"]:
        s = df[df["strategy"] == strategy]
        entered = s[s["decision"] == "ENTER"]
        pnl = pd.to_numeric(entered["total_pnl"], errors="coerce").fillna(0.0)
        wins = int((pnl > 0).sum())
        losses = int((pnl < 0).sum())
        flats = int((pnl == 0).sum())
        eq = pnl.cumsum()
        dd = eq - eq.cummax()
        rows.append(
            {
                "Strategy": strategy,
                "Days Evaluated": int(len(s)),
                "Trades": int(len(entered)),
                "Win Rate": round((wins / len(entered)) * 100.0, 2) if len(entered) else 0.0,
                "Wins": wins,
                "Losses": losses,
                "Flat": flats,
                "Total PnL": round(float(pnl.sum()), 2),
                "Avg/Trade": round(float(pnl.mean()), 2) if len(entered) else 0.0,
                "Median/Trade": round(float(pnl.median()), 2) if len(entered) else 0.0,
                "Max Drawdown": round(float(dd.min()), 2) if len(entered) else 0.0,
                "Best Trade": round(float(pnl.max()), 2) if len(entered) else 0.0,
                "Worst Trade": round(float(pnl.min()), 2) if len(entered) else 0.0,
            }
        )
    return pd.DataFrame(rows)


def _to_markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "| empty |\n|---|\n| no rows |\n"
    cols = [str(c) for c in df.columns.tolist()]
    lines = []
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for row in df.to_dict(orient="records"):
        vals = [str(row.get(c, "")) for c in cols]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = _parse_args()
    api_key = os.getenv("DATABENTO_API_KEY", "")
    if not api_key:
        raise SystemExit("DATABENTO_API_KEY is required")

    cfg = _load_cfg(Path(args.config))
    tz = ZoneInfo(cfg.timezone)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.raw_cache_dir)

    daily_df = pd.read_csv(args.daily_csv)
    daily_df["date"] = pd.to_datetime(daily_df["date"]).dt.date
    if args.start_date:
        daily_df = daily_df[daily_df["date"] >= _parse_date(args.start_date)]
    if args.end_date:
        daily_df = daily_df[daily_df["date"] <= _parse_date(args.end_date)]
    daily_df = daily_df.sort_values("date").reset_index(drop=True)

    macro_blocked = set(
        daily_df[daily_df["reason"].fillna("").astype(str).str.startswith("macro event filter")]["date"].tolist()
    )
    market_rows = {
        row["date"]: {
            "spx": float(row["spx_entry"]) if pd.notna(row["spx_entry"]) else math.nan,
            "vix": float(row["vix_entry"]) if pd.notna(row["vix_entry"]) else math.nan,
        }
        for row in daily_df.to_dict(orient="records")
    }

    client = DBCacheClient(cache_dir, api_key)
    records: list[SimResult] = []

    max_margin_dollars = float(args.capital) * float(args.max_margin_pct)
    total_days = len(daily_df)

    for i, d in enumerate(daily_df["date"].tolist(), start=1):
        if i % max(int(args.status_every), 1) == 0:
            print(f"[{i}/{total_days}] processing {d.isoformat()} ...")

        if d.weekday() not in cfg.run_days:
            for strategy in ["BULL_PUT_SPREAD", "PUT_BWB", "IRON_CONDOR", "IRON_FLY", "HARVESTER_100_V3"]:
                records.append(SimResult(strategy=strategy, trade_date=d, decision="SKIP", reason="day-of-week filter"))
            continue

        if d in macro_blocked:
            for strategy in ["BULL_PUT_SPREAD", "PUT_BWB", "IRON_CONDOR", "IRON_FLY", "HARVESTER_100_V3"]:
                records.append(SimResult(strategy=strategy, trade_date=d, decision="SKIP", reason="macro event filter"))
            continue

        m = market_rows.get(d)
        if not m or not math.isfinite(m["spx"]) or not math.isfinite(m["vix"]):
            for strategy in ["BULL_PUT_SPREAD", "PUT_BWB", "IRON_CONDOR", "IRON_FLY", "HARVESTER_100_V3"]:
                records.append(SimResult(strategy=strategy, trade_date=d, decision="SKIP", reason="missing SPX/VIX"))
            continue

        spx = m["spx"]
        vix = m["vix"]
        regime = _resolve_regime(cfg, vix)
        if regime is None:
            for strategy in ["BULL_PUT_SPREAD", "PUT_BWB", "IRON_CONDOR", "IRON_FLY", "HARVESTER_100_V3"]:
                records.append(SimResult(strategy=strategy, trade_date=d, decision="SKIP", reason="VIX tail-risk zone"))
            continue

        defs = client.get_df(
            dataset="OPRA.PILLAR",
            schema="definition",
            stype_in="parent",
            symbols="SPXW.OPT",
            start=d,
            end=d + timedelta(days=1),
        )
        if defs.empty or "raw_symbol" not in defs.columns:
            for strategy in ["BULL_PUT_SPREAD", "PUT_BWB", "IRON_CONDOR", "IRON_FLY", "HARVESTER_100_V3"]:
                records.append(SimResult(strategy=strategy, trade_date=d, decision="SKIP", reason="no option definitions"))
            continue

        parsed = defs["raw_symbol"].astype(str).map(_occ_parse)
        defs = defs.assign(
            expiry=[p[0] for p in parsed],
            right=[p[1] for p in parsed],
            strike=[p[2] for p in parsed],
        )
        defs = defs.dropna(subset=["expiry", "right", "strike"])
        allowed_exp = {d + timedelta(days=int(k)) for k in cfg.dte_candidates}
        defs = defs[defs["expiry"].isin(allowed_exp)]
        if defs.empty:
            for strategy in ["BULL_PUT_SPREAD", "PUT_BWB", "IRON_CONDOR", "IRON_FLY", "HARVESTER_100_V3"]:
                records.append(SimResult(strategy=strategy, trade_date=d, decision="SKIP", reason="no allowed expiries"))
            continue

        defs = defs.groupby("raw_symbol").last()[["expiry", "right", "strike"]].reset_index().rename(
            columns={"raw_symbol": "symbol"}
        )
        all_candidates = _build_candidates(cfg, d, spx, regime, defs)
        if not all_candidates:
            for strategy in ["BULL_PUT_SPREAD", "PUT_BWB", "IRON_CONDOR", "IRON_FLY", "HARVESTER_100_V3"]:
                records.append(SimResult(strategy=strategy, trade_date=d, decision="SKIP", reason="no structural candidates"))
            continue

        entry_start_et = datetime.combine(d, dt_time(9, 45), tz)
        entry_end_et = entry_start_et + timedelta(minutes=1)
        candidate_symbols = sorted({leg.symbol for c in all_candidates for leg in c.legs})
        entry_df = client.get_window(
            symbols=candidate_symbols,
            start_utc=entry_start_et.astimezone(timezone.utc),
            end_utc=entry_end_et.astimezone(timezone.utc),
        )
        qmap = _entry_qmap(entry_df, candidate_symbols)

        quoted_candidates: list[Candidate] = []
        for cand in all_candidates:
            q = _entry_quote(cand.legs, qmap)
            if q is None:
                continue
            bid, ask, mid = q
            if mid < cfg.min_credit:
                continue
            cand.credit_bid = bid
            cand.credit_ask = ask
            cand.credit_mid = mid
            cand.max_loss_per_contract = _estimate_max_loss_dollars(cand.legs, mid)
            if cand.max_loss_per_contract <= 0:
                continue
            quoted_candidates.append(cand)

        chosen = _select_candidates_by_strategy(quoted_candidates, cfg)
        for strat in ["BULL_PUT_SPREAD", "PUT_BWB", "IRON_CONDOR", "IRON_FLY"]:
            cand = chosen.get(strat)
            if cand is None:
                records.append(SimResult(strategy=strat, trade_date=d, decision="SKIP", reason="no credit-qualified candidate"))
                continue

            by_margin = int(max_margin_dollars // max(cand.max_loss_per_contract, 1.0))
            contracts = max(0, min(int(args.max_contracts), by_margin))
            if contracts <= 0:
                records.append(SimResult(strategy=strat, trade_date=d, decision="SKIP", reason="margin sizing zero"))
                continue

            syms = sorted({leg.symbol for leg in cand.legs})
            end_et = datetime.combine(d, dt_time(16, 0), tz)
            qdf = client.get_window(
                symbols=syms,
                start_utc=entry_start_et.astimezone(timezone.utc),
                end_utc=end_et.astimezone(timezone.utc),
            )
            idx = pd.date_range(start=entry_start_et, end=end_et, freq="1min", tz=tz)
            norm = _normalize_quotes(qdf, syms, idx, tz)
            sim = _simulate_standard(cand, contracts, norm, d, cfg)
            records.append(sim)

        # 5th design is based on single BPS candidate with separate risk engine.
        bps = chosen.get("BULL_PUT_SPREAD")
        if bps is None:
            records.append(SimResult(strategy="HARVESTER_100_V3", trade_date=d, decision="SKIP", reason="no BPS candidate"))
        else:
            syms = sorted({leg.symbol for leg in bps.legs})
            noon_et = datetime.combine(d, dt_time(12, 0), tz)
            qdf = client.get_window(
                symbols=syms,
                start_utc=entry_start_et.astimezone(timezone.utc),
                end_utc=noon_et.astimezone(timezone.utc),
            )
            idx = pd.date_range(start=entry_start_et, end=noon_et, freq="1min", tz=tz)
            norm = _normalize_quotes(qdf, syms, idx, tz)
            sim = _simulate_harvester(bps, norm, d)
            records.append(sim)

    out_df = pd.DataFrame([asdict(r) for r in records])
    out_df["trade_date"] = out_df["trade_date"].astype(str)
    out_df.to_csv(out_dir / "strategy_trade_results.csv", index=False)

    summary = _summarize(records)
    summary.to_csv(out_dir / "strategy_summary.csv", index=False)
    (out_dir / "strategy_summary.md").write_text(_to_markdown_table(summary), encoding="utf-8")

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "assumptions": {
            "capital": args.capital,
            "max_margin_pct": args.max_margin_pct,
            "max_contracts": args.max_contracts,
            "standard_exit_logic": "stop/profit/friday-force/15:45-force",
            "harvester_logic": "target/ratchet/scale-out/noon-force",
            "market_data_source": "OPRA.PILLAR cbbo-1m + cached SPX/VIX entries from daily_results.csv",
        },
        "rows": int(len(out_df)),
        "summary_rows": summary.to_dict(orient="records"),
    }
    (out_dir / "snapshot.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"wrote: {out_dir / 'strategy_trade_results.csv'}")
    print(f"wrote: {out_dir / 'strategy_summary.csv'}")
    print(f"wrote: {out_dir / 'strategy_summary.md'}")
    print(f"wrote: {out_dir / 'snapshot.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
