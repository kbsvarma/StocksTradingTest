"""
Jade Lizard Backtest — SPXW 0DTE
=================================
Extended version of backtest_databento.py that adds a bear call spread
to each bull put spread, forming a Jade Lizard (Modified):

    Leg 1: Short OTM put         (same as BPS)
    Leg 2: Long further OTM put  (same as BPS)
    Leg 3: Short OTM call        (new — 1% above SPX)
    Leg 4: Long further OTM call (new — short_call + CALL_WIDTH)

All Databento quote data is read from the existing local cache — no new
API calls or costs required.

Usage
-----
    python backtest_jade_lizard.py --config config_xsp.yaml \\
        --start-date 2016-01-01 --end-date 2026-04-30 \\
        --raw-cache-dir backtests/ten_year/raw_dbn_cache \\
        --output-dir backtests/jade_lizard

Tuning knobs (edit constants below or pass as args)
-----
    CALL_OTM_PCT   : how far OTM the short call is (default 1.0%)
    CALL_WIDTH_PT  : call spread width in points    (default 25)
"""

from __future__ import annotations

import argparse
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

# reuse all helpers from the original backtest
from backtest_databento import (
    Backtester,
    Candidate,
    ExitResult,
    Regime,
    _MacroLogger,
    _build_parser as _bps_build_parser,
    _configure_logger,
    _fmt_et,
    _occ_parse,
    _parse_date,
    _resolve_regime,
)
from config_loader import BotConfig, load_config

# ── Jade Lizard config ────────────────────────────────────────────────────────
CALL_OTM_PCT  = 0.010   # short call placed 1.0% above SPX
CALL_WIDTH_PT = 25.0    # long call is 25pts above short call


# ── extended candidate dataclass ─────────────────────────────────────────────

@dataclass(slots=True)
class JLCandidate:
    # put spread (identical to BPS Candidate)
    expiry:          date
    dte:             int
    short_put:       float
    long_put:        float
    short_put_sym:   str
    long_put_sym:    str
    put_bid:         float
    put_ask:         float
    put_mid:         float
    # call spread
    short_call:      float
    long_call:       float
    short_call_sym:  str
    long_call_sym:   str
    call_bid:        float
    call_ask:        float
    call_mid:        float
    # combined
    total_mid:       float
    no_upside_risk:  bool


# ── backtester subclass ───────────────────────────────────────────────────────

class JadeLizardBacktester(Backtester):
    """Inherits all IBKR connectivity and Databento caching from Backtester.
    Overrides the evaluation, definitions, and exit logic for 4-leg JL."""

    def _definitions_snapshot_jl(
        self, current_day: date
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Return (puts_snap, calls_snap) — both sides from the same query."""
        end_day = current_day + timedelta(days=1)
        df = self._db_get_range_df(
            dataset="OPRA.PILLAR",
            schema="definition",
            stype_in="parent",
            symbols="SPXW.OPT",
            start=current_day,
            end=end_day,
        )
        if df.empty or "raw_symbol" not in df.columns:
            return pd.DataFrame(), pd.DataFrame()

        parsed = df["raw_symbol"].astype(str).map(_occ_parse)
        df = df.assign(
            expiry=[p[0] for p in parsed],
            right=[p[1] for p in parsed],
            strike=[p[2] for p in parsed],
        )
        df = df.dropna(subset=["expiry", "right", "strike"])
        allowed_exp = {current_day + timedelta(days=dte) for dte in self.cfg.dte_candidates}
        df = df[df["expiry"].isin(allowed_exp)]
        if df.empty:
            return pd.DataFrame(), pd.DataFrame()

        def to_snap(sub: pd.DataFrame) -> pd.DataFrame:
            return (
                sub.groupby("raw_symbol")
                .last()[["expiry", "strike"]]
                .reset_index()
                .rename(columns={"raw_symbol": "symbol"})
            )

        puts  = to_snap(df[df["right"] == "P"])
        calls = to_snap(df[df["right"] == "C"])
        return puts, calls

    def _build_jl_plans(
        self,
        current_day: date,
        spx: float,
        regime: Regime,
        puts: pd.DataFrame,
        calls: pd.DataFrame,
    ) -> list[dict]:
        """Build (put_spread + call_spread) candidate plan dicts."""
        plans: list[dict] = []
        otm_points = [
            regime.low,
            round((regime.low + regime.high) / 2.0, 4),
            regime.high,
        ]

        for dte in self.cfg.dte_candidates:
            expiry = current_day + timedelta(days=dte)

            # ── put side ─────────────────────────────────────────────────────
            put_rows = puts[puts["expiry"] == expiry]
            if put_rows.empty:
                continue
            put_by_strike: dict[float, str] = {}
            for row in put_rows.to_dict(orient="records"):
                k = float(row["strike"])
                if k not in put_by_strike:
                    put_by_strike[k] = str(row["symbol"])
            put_strikes = sorted(put_by_strike)
            put_strike_set = set(put_strikes)

            # ── call side ────────────────────────────────────────────────────
            call_rows = calls[calls["expiry"] == expiry]
            if call_rows.empty:
                continue
            call_by_strike: dict[float, str] = {}
            for row in call_rows.to_dict(orient="records"):
                k = float(row["strike"])
                if k not in call_by_strike:
                    call_by_strike[k] = str(row["symbol"])
            call_strikes = sorted(call_by_strike)

            # pick call spread strikes (fixed per day, independent of put OTM)
            call_target   = spx * (1.0 + CALL_OTM_PCT)
            above         = [s for s in call_strikes if s >= call_target]
            if not above:
                continue
            short_call     = min(above)
            long_call      = short_call + CALL_WIDTH_PT
            # find nearest long call strike
            long_call_cands = [s for s in call_strikes if s >= long_call]
            if not long_call_cands:
                continue
            long_call = min(long_call_cands)
            if long_call not in call_by_strike or short_call not in call_by_strike:
                continue

            # ── combine: one put spread config per otm level ──────────────────
            for otm in otm_points:
                target_level = spx * (1.0 - otm)
                cands = [s for s in put_strikes if s <= target_level]
                if not cands:
                    continue
                short_base = max(cands)
                base_idx   = put_strikes.index(short_base)

                for offset in range(0, 10):
                    idx = base_idx - offset
                    if idx < 0:
                        break
                    short_put  = put_strikes[idx]
                    long_put   = round(short_put - self.cfg.spread_width, 3)
                    if long_put not in put_strike_set:
                        continue
                    plans.append(dict(
                        expiry          = expiry,
                        dte             = dte,
                        otm_pct         = otm,
                        target_level    = target_level,
                        short_put       = short_put,
                        long_put        = long_put,
                        short_put_sym   = put_by_strike[short_put],
                        long_put_sym    = put_by_strike[long_put],
                        short_call      = short_call,
                        long_call       = long_call,
                        short_call_sym  = call_by_strike[short_call],
                        long_call_sym   = call_by_strike[long_call],
                    ))
        return plans

    def _pick_jl_candidate(
        self, plans: list[dict], quotes: pd.DataFrame
    ) -> Optional[JLCandidate]:
        if not plans or quotes.empty:
            return None

        qmap = {str(r["symbol"]): r for r in quotes.to_dict(orient="records")}
        best: Optional[JLCandidate] = None
        best_score = math.inf

        for p in plans:
            sq = qmap.get(p["short_put_sym"])
            lq = qmap.get(p["long_put_sym"])
            scq = qmap.get(p["short_call_sym"])
            lcq = qmap.get(p["long_call_sym"])
            if not (sq and lq and scq and lcq):
                continue

            # put spread mid
            put_bid = float(sq["bid"]) - float(lq["ask"])
            put_ask = float(sq["ask"]) - float(lq["bid"])
            if put_bid <= 0 or put_ask <= 0:
                continue
            put_mid = round((put_bid + put_ask) / 2.0, 2)
            if put_mid < self.cfg.min_credit:
                continue

            # call spread mid (may be near zero for deep OTM 0DTE)
            call_bid = max(0.0, float(scq["bid"]) - float(lcq["ask"]))
            call_ask = max(0.0, float(scq["ask"]) - float(lcq["bid"]))
            call_mid = round((call_bid + call_ask) / 2.0, 2) if (call_bid > 0 and call_ask > 0) else 0.0

            total_mid = round(put_mid + call_mid, 2)

            # Jade Lizard no-upside-risk check
            # total_credit (per share) >= call_spread_width (per share)
            # call spread max loss per share = long_call - short_call
            call_width_pts = p["long_call"] - p["short_call"]
            no_upside = total_mid >= call_width_pts

            # score by put credit only — call spread is additive, not a substitute
            score = abs(self.cfg.target_credit - put_mid)
            if score < best_score:
                best_score = score
                best = JLCandidate(
                    expiry         = p["expiry"],
                    dte            = int(p["dte"]),
                    short_put      = float(p["short_put"]),
                    long_put       = float(p["long_put"]),
                    short_put_sym  = p["short_put_sym"],
                    long_put_sym   = p["long_put_sym"],
                    put_bid        = round(put_bid, 2),
                    put_ask        = round(put_ask, 2),
                    put_mid        = put_mid,
                    short_call     = float(p["short_call"]),
                    long_call      = float(p["long_call"]),
                    short_call_sym = p["short_call_sym"],
                    long_call_sym  = p["long_call_sym"],
                    call_bid       = round(call_bid, 2),
                    call_ask       = round(call_ask, 2),
                    call_mid       = call_mid,
                    total_mid      = total_mid,
                    no_upside_risk = no_upside,
                )
        return best

    def _load_quad_quotes(
        self,
        syms: list[str],
        start_et: datetime,
        end_et: datetime,
    ) -> pd.DataFrame:
        """Load 1-min CBBO for 4 symbols and return a wide dataframe."""
        df = self._db_get_range_df(
            dataset="OPRA.PILLAR",
            schema="cbbo-1m",
            stype_in="raw_symbol",
            symbols=syms,
            start=start_et.astimezone(timezone.utc),
            end=end_et.astimezone(timezone.utc),
        )
        if df.empty:
            return df
        if "publisher_id" in df.columns:
            df = df[df["publisher_id"] == 30]
        if df.empty:
            return df

        df = df[df["symbol"].isin(syms)].sort_index()
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
        for sym, col_prefix in zip(syms, ["sp", "lp", "sc", "lc"]):
            out[f"{col_prefix}_bid"] = wide.get(("bid_px_00", sym))
            out[f"{col_prefix}_ask"] = wide.get(("ask_px_00", sym))
        return out

    @staticmethod
    def _put_mark(row: pd.Series) -> Optional[float]:
        """Put spread mark only — used for stop/target logic."""
        sp_b = float(row.get("sp_bid") or 0)
        sp_a = float(row.get("sp_ask") or 0)
        lp_b = float(row.get("lp_bid") or 0)
        lp_a = float(row.get("lp_ask") or 0)
        if sp_b <= 0 or sp_a <= 0:
            return None
        put_bid = sp_b - lp_a
        put_ask = sp_a - lp_b
        if put_bid <= 0 or put_ask <= 0:
            return None
        return round((put_bid + put_ask) / 2.0, 2)

    @staticmethod
    def _call_mark(row: pd.Series) -> float:
        """Call spread mark — returns 0 if quotes absent/invalid."""
        sc_b = float(row.get("sc_bid") or 0)
        sc_a = float(row.get("sc_ask") or 0)
        lc_b = float(row.get("lc_bid") or 0)
        lc_a = float(row.get("lc_ask") or 0)
        if sc_b > 0 and sc_a > 0:
            cb = sc_b - lc_a
            ca = sc_a - lc_b
            if cb > 0 and ca > 0:
                return round((cb + ca) / 2.0, 2)
        return 0.0

    def _simulate_jl_exit(
        self,
        current_day: date,
        cand: JLCandidate,
        entry_dt_et: datetime,
        put_stop: float,
        put_target: float,
    ) -> tuple[ExitResult, float]:
        """Returns (put_exit_result, call_final_mark).
        Put spread: stops/targets on put mark only.
        Call spread: always rides to EOD; final mark captured at put exit time."""
        friday_force = datetime.combine(current_day, self.cfg.friday_forced_close_time(), self.tz)
        eod_check    = datetime.combine(current_day, self.cfg.eod_check_time(), self.tz)
        market_end   = datetime.combine(current_day, dt_time(16, 0), self.tz)

        syms = [
            cand.short_put_sym, cand.long_put_sym,
            cand.short_call_sym, cand.long_call_sym,
        ]
        qf = self._load_quad_quotes(syms, entry_dt_et, market_end)
        if qf.empty:
            return ExitResult("data_unavailable", entry_dt_et, 0.0, "no quad quotes"), 0.0

        last_put_mark:  Optional[float] = None
        last_call_mark: float = 0.0

        for ts_et, row in qf.iterrows():
            pm = self._put_mark(row)
            cm = self._call_mark(row)
            if pm is not None:
                last_put_mark = pm
            if cm > 0:
                last_call_mark = cm

            if pm is not None:
                if pm >= put_stop:
                    return ExitResult("stop_loss", ts_et, pm, "put mark>=stop"), last_call_mark
                if pm <= put_target:
                    return ExitResult("profit_target", ts_et, put_target, "put mark<=target"), last_call_mark

            if current_day.weekday() == 4 and ts_et >= friday_force:
                px = last_put_mark or 0.0
                return ExitResult("friday_force_close", ts_et, px, "friday hard close"), last_call_mark

            if ts_et >= eod_check:
                spx_eod = self._index_value(self.spx_contract, eod_check)
                if spx_eod is None:
                    px = last_put_mark or 0.0
                    return ExitResult("eod_force_close", ts_et, px, "missing SPX@eod"), last_call_mark

                if (spx_eod - cand.short_put) > 50.0 and (cand.short_call - spx_eod) > 50.0:
                    return ExitResult("expiry", market_end, 0.0, "both sides safe"), 0.0

                if ts_et >= market_end:
                    px = last_put_mark or 0.0
                    return ExitResult("eod_force_close", market_end, px, "fell through"), last_call_mark

        px = last_put_mark or 0.0
        return ExitResult("eod_force_close", market_end, px, "fell through"), last_call_mark

    def _evaluate_day(
        self,
        current_day: date,
        day_record: dict,
        trade_rows: list[dict],
    ) -> None:
        if not self.cfg.is_trade_day(current_day):
            day_record["reason"] = "day-of-week filter"
            return

        blocking, ev = self.macro.has_blocking_event(current_day)
        if blocking:
            day_record["reason"] = f"macro event filter: {ev}"
            return

        entry_dt_et = datetime.combine(current_day, self.cfg.entry_start_time(), self.tz)
        spx_entry   = self._index_value(self.spx_contract, entry_dt_et)
        vix_entry   = self._index_value(self.vix_contract, entry_dt_et)
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

        puts, calls = self._definitions_snapshot_jl(current_day)
        if puts.empty or calls.empty:
            day_record["reason"] = "no option definitions for day"
            return

        plans = self._build_jl_plans(current_day, spx_entry, regime, puts, calls)
        if not plans:
            day_record["reason"] = "no JL candidate plans"
            return

        all_syms = sorted({
            s for p in plans
            for s in [p["short_put_sym"], p["long_put_sym"],
                      p["short_call_sym"], p["long_call_sym"]]
        })
        entry_quotes = self._entry_quotes(current_day, all_syms)
        if entry_quotes.empty:
            day_record["reason"] = "no entry quotes"
            return

        cand = self._pick_jl_candidate(plans, entry_quotes)
        if cand is None:
            day_record["reason"] = "no credit-qualified JL candidate"
            return

        # sizing on put spread max risk (same as BPS)
        per_spread_risk = max((self.cfg.spread_width - cand.put_mid) * 100.0, 1.0)
        by_margin_cap   = int(self.cfg.max_margin_dollars() // per_spread_risk)
        contracts       = max(0, min(self.cfg.max_contracts, by_margin_cap))
        if contracts <= 0:
            day_record["reason"] = "margin cap allows zero contracts"
            return

        # stop and target on PUT side only — call rides to EOD independently
        put_stop   = round(cand.put_mid * self.cfg.stop_multiplier, 2)
        put_target = round(cand.put_mid * (1 - self.cfg.profit_target_pct), 2)

        exit_result, call_final_mark = self._simulate_jl_exit(
            current_day, cand, entry_dt_et, put_stop, put_target
        )

        # put spread P&L
        if exit_result.reason == "expiry":
            put_pnl_per = round(cand.put_mid * 100.0, 2)
        else:
            put_pnl_per = round((cand.put_mid - exit_result.exit_price) * 100.0, 2)

        # call spread P&L — credit collected minus residual mark at EOD
        # if call expires worthless (mark→0), full credit kept
        call_pnl_per = round((cand.call_mid - call_final_mark) * 100.0, 2)

        pnl_per   = round(put_pnl_per + call_pnl_per, 2)
        total_pnl = round(pnl_per * contracts, 2)

        day_record.update(
            decision        = "ENTER",
            reason          = f"JL enter ({regime.name}) no_upside={cand.no_upside_risk}",
            expiry          = cand.expiry.isoformat(),
            dte             = cand.dte,
            short_strike    = cand.short_put,
            long_strike     = cand.long_put,
            entry_credit    = cand.total_mid,
            contracts       = contracts,
            estimated_margin= round(per_spread_risk * contracts, 2),
            exit_time       = _fmt_et(exit_result.exit_time_et),
            exit_price      = round(exit_result.exit_price, 2),
            exit_reason     = exit_result.reason,
            pnl_per_contract= pnl_per,
            total_pnl       = total_pnl,
            win_loss        = "Win" if total_pnl >= 0 else "Loss",
        )

        trade_rows.append(dict(
            Date                   = current_day.isoformat(),
            **{"Entry time":         entry_dt_et.strftime("%H:%M:%S")},
            **{"SPX price at entry": round(spx_entry, 4)},
            **{"VIX at entry":       round(vix_entry, 4)},
            **{"Short put":          cand.short_put},
            **{"Long put":           cand.long_put},
            **{"Short call":         cand.short_call},
            **{"Long call":          cand.long_call},
            **{"Put credit":         cand.put_mid},
            **{"Call credit":        cand.call_mid},
            **{"Total credit":       cand.total_mid},
            **{"No upside risk":     cand.no_upside_risk},
            Contracts              = contracts,
            **{"Exit time":          exit_result.exit_time_et.strftime("%H:%M:%S")},
            **{"Put exit mark":      round(exit_result.exit_price, 2)},
            **{"Call final mark":    round(call_final_mark, 2)},
            **{"Put PnL":            round(put_pnl_per * contracts, 2)},
            **{"Call PnL":           round(call_pnl_per * contracts, 2)},
            **{"PnL per contract":   pnl_per},
            **{"Total PnL":          total_pnl},
            **{"Win/Loss":           "Win" if total_pnl >= 0 else "Loss"},
            **{"Exit reason":        exit_result.reason},
            **{"Notes":              exit_result.note},
            Expiry                 = cand.expiry.isoformat(),
            DTE                    = cand.dte,
            **{"Call OTM pct":      CALL_OTM_PCT},
            **{"Call width pt":     CALL_WIDTH_PT},
        ))


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = _bps_build_parser()
    p.description = "Jade Lizard backtest (BPS + bear call spread) via Databento cache"
    p.add_argument("--call-otm-pct",   type=float, default=CALL_OTM_PCT,
                   help=f"Short call OTM%% (default {CALL_OTM_PCT})")
    p.add_argument("--call-width-pt",  type=float, default=CALL_WIDTH_PT,
                   help=f"Call spread width in points (default {CALL_WIDTH_PT})")
    return p


def main() -> int:
    args = _build_parser().parse_args()
    if not os.getenv("DATABENTO_API_KEY"):
        raise SystemExit("DATABENTO_API_KEY is not set")

    # allow CLI overrides
    global CALL_OTM_PCT, CALL_WIDTH_PT
    CALL_OTM_PCT  = args.call_otm_pct
    CALL_WIDTH_PT = args.call_width_pt

    cfg        = load_config(args.config)
    output_dir = Path(args.output_dir)
    logger     = _configure_logger(output_dir)

    today_et   = datetime.now(ZoneInfo(cfg.timezone)).date()
    start_date = _parse_date(args.start_date) if args.start_date else (today_et - timedelta(days=3650))
    end_date   = _parse_date(args.end_date)   if args.end_date   else today_et

    logger.info(
        f"Jade Lizard backtest  {start_date} → {end_date}  "
        f"call_otm={CALL_OTM_PCT*100:.1f}%  call_width={CALL_WIDTH_PT}pt"
    )

    raw_cache_dir  = Path(args.raw_cache_dir) if args.raw_cache_dir else None
    daily_csv_path = Path(args.daily_csv)     if args.daily_csv    else None
    bt = JadeLizardBacktester(cfg, output_dir, logger,
                               raw_cache_dir=raw_cache_dir,
                               daily_csv_path=daily_csv_path)
    try:
        daily_df, trades_df, summary = bt.run(
            start_date = start_date,
            end_date   = end_date,
            resume     = args.resume,
            max_days   = args.max_days,
            sleep_ms   = args.sleep_ms,
        )
    finally:
        bt.close()

    logger.info(
        f"done  days={len(daily_df)}  trades={len(trades_df)}  "
        f"total_pnl={summary.get('total_pnl')}  "
        f"win_rate={summary.get('win_rate')}  "
        f"sharpe=n/a (compute from trades_df)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
