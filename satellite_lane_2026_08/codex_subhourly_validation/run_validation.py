"""Independent causal audit of the sub-hourly breadth-satellite claim.

This file does not import or execute ``run_subhourly.py``.  It rebuilds the
bars and simulations from the locked one-minute caches, reproduces the
reported implementation, then corrects two event-time problems:

1. Left-labelled hourly features are available at label + 60 minutes, not at
   the label.  Sub-hourly bars therefore map features by their close time.
2. A core stop carrying the same hourly label as a satellite entry occurs
   after that bar's open.  Core entries at t count; exits at t do not remove
   exposure until after the entry decision.

It also tests a matched two-hour channel-maturation horizon.  The reported
study used two *bars* for every frequency (30 minutes at 15m versus two hours
at 60m), so its headline cannot by itself identify entry latency as the cause.
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "reentry_regime_2026_07"))
import engine_v as ev  # noqa: E402

rg = ev.rg
import strategy  # noqa: E402
from rotation import monday_of_week  # noqa: E402

START = pd.Timestamp("2011-01-01", tz="UTC")
END = pd.Timestamp("2026-01-01", tz="UTC")
FREQ_MINUTES = {"60min": 60, "30min": 30, "20min": 20, "15min": 15,
                "10min": 10, "5min": 5}


def load_native(sym: str, freq: str) -> pd.DataFrame:
    files = sorted(glob.glob(str(rg.SRC[sym])))
    if not files:
        raise FileNotFoundError(f"missing one-minute cache for {sym}")
    raw = pd.concat([pd.read_parquet(f) for f in files]).sort_index()
    raw = raw[~raw.index.duplicated(keep="last")]
    raw["roll_flag"] = (
        raw["instrument_id"] != raw["instrument_id"].shift()).astype(int)
    grouped = raw.resample(freq, label="left", closed="left")
    bars = pd.DataFrame({
        "open": grouped["open"].first(),
        "high": grouped["high"].max(),
        "low": grouped["low"].min(),
        "close": grouped["close"].last(),
        "roll": grouped["roll_flag"].max(),
    }).dropna(subset=["close"])
    bars["roll"] = bars["roll"].fillna(0).astype(int)
    return bars


def map_hourly_causal(series: pd.Series, target: pd.DatetimeIndex,
                      target_minutes: int) -> pd.Series:
    """Map a left-labelled hourly feature using its actual availability."""
    available = series.copy()
    available.index = available.index + pd.Timedelta(hours=1)
    target_close = target + pd.Timedelta(minutes=target_minutes)
    mapped = available.reindex(target_close, method="ffill")
    mapped.index = target
    return mapped


def daily_mtm_equity(trades: pd.DataFrame, bars60: dict[str, pd.DataFrame],
                     friction: float = 3.5) -> pd.Series:
    days = pd.date_range(START, END - pd.Timedelta(days=1), freq="D")
    equity = pd.Series(0.0, index=days)
    daily = {s: b["close"].resample("1D").last().dropna()
             for s, b in bars60.items()}
    for sym in bars60:
        selected = trades[trades["sym"] == sym]
        marks = daily[sym].reindex(days, method="ffill")
        for tr in selected.itertuples():
            qty = int(getattr(tr, "qty", 1))
            mult = rg.MULT[sym]
            live = ((days >= tr.entry_time.normalize())
                    & (days < tr.exit_time.normalize()))
            equity[live] += (marks[live] - tr.entry_px) * mult * qty
            equity[days >= tr.exit_time.normalize()] += (
                (tr.exit_px - tr.entry_px) * mult * qty - friction * qty)
    return equity


def metrics(core: pd.DataFrame, satellite: pd.DataFrame,
            bars60: dict[str, pd.DataFrame], friction: float = 3.5) -> dict:
    core25 = core[(core.exit_time >= START) & (core.exit_time < END)].copy()
    sat25 = satellite[(satellite.exit_time >= START)
                      & (satellite.exit_time < END)].copy()
    sat_pnl = ((sat25.exit_px - sat25.entry_px)
               * sat25.sym.map(rg.MULT) - friction)
    core_pnl = ((core25.exit_px - core25.entry_px)
                * core25.sym.map(rg.MULT) * core25.qty
                - friction * core25.qty)
    exit_pnl = pd.concat([
        pd.Series(core_pnl.to_numpy(), index=pd.DatetimeIndex(core25.exit_time)),
        pd.Series(sat_pnl.to_numpy(), index=pd.DatetimeIndex(sat25.exit_time)),
    ]).sort_index()
    combined = pd.concat([core25, sat25.assign(qty=1)], ignore_index=True)
    equity = daily_mtm_equity(combined, bars60, friction)
    drawdown = equity - equity.cummax()
    yearly = exit_pnl.groupby(exit_pnl.index.year).sum()
    sat_by_year = pd.Series(sat_pnl.to_numpy(),
                            index=pd.DatetimeIndex(sat25.exit_time)).sort_index()
    return {
        "sat": float(sat_pnl.sum()),
        "n": int(len(sat25)),
        "combined": float(exit_pnl.sum()),
        "max_dd": float(drawdown.min()),
        "ratio": float(exit_pnl.sum() / abs(drawdown.min())),
        "worst": float(yearly.min()),
        "worst_year": int(yearly.idxmin()),
        "h1": float(sat_by_year.loc[:"2017"].sum()),
        "h2": float(sat_by_year.loc["2018":].sum()),
        "yearly": {str(int(k)): float(v)
                   for k, v in sat_by_year.groupby(sat_by_year.index.year).sum().items()},
    }


def main() -> None:
    print("loading independent 1m -> 5/10/15/30/60m bars", flush=True)
    freqs = tuple(FREQ_MINUTES)
    bars = {s: {f: load_native(s, f) for f in freqs}
            for s in ("NQ", "ES", "YM")}
    bars60 = {s: bars[s]["60min"] for s in bars}
    daily = {s: b["close"].resample("1D").last().dropna()
             for s, b in bars60.items()}
    rg.START = START
    weights = rg.weekly_weights(
        daily, 35, "aggressive300",
        min(b.index.min() for b in bars60.values()),
        max(b.index.max() for b in bars60.values()))

    core_parts = []
    for sym in bars:
        def qty(ts, s=sym):
            week = monday_of_week(ts)
            return int(weights.loc[week, s]) if week in weights.index else 1
        trade = rg.simulate_weighted(bars60[sym], sym, qty)
        core_parts.append(trade[trade.qty > 0].copy())
    core = pd.concat(core_parts, ignore_index=True)

    # Prefix sums implement exposure at the entry OPEN: entries at t count,
    # exits carrying label t have not happened yet.
    ent = core.sort_values("entry_time")
    ext = core.sort_values("exit_time")
    ent_t = pd.DatetimeIndex(ent.entry_time).asi8
    ext_t = pd.DatetimeIndex(ext.exit_time).asi8
    ent_q = ent.qty.to_numpy().cumsum()
    ext_q = ext.qty.to_numpy().cumsum()

    def causal_core_qty(ts: pd.Timestamp) -> int:
        value = ts.value
        ie = np.searchsorted(ent_t, value, side="right")
        ix = np.searchsorted(ext_t, value, side="left")
        entered = ent_q[ie - 1] if ie else 0
        exited = ext_q[ix - 1] if ix else 0
        return int(entered - exited)

    naive_events = pd.concat([
        pd.Series(core.qty.to_numpy(), index=pd.DatetimeIndex(core.entry_time)),
        pd.Series(-core.qty.to_numpy(), index=pd.DatetimeIndex(core.exit_time)),
    ]).groupby(level=0).sum().sort_index().cumsum()

    hourly_frames = {s: strategy.signal_frame(bars60[s], rg.SCFG)
                     for s in bars}

    def benched(sym: str, ts: pd.Timestamp) -> bool:
        week = monday_of_week(ts)
        if week not in weights.index:
            return False
        row = weights.loc[week]
        return int(row[sym]) == 0 and sum(int(row[s]) == 0 for s in bars) < 3

    feature_cache: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}

    def features(sym: str, freq: str, stop_basis: str,
                 causal: bool) -> tuple[np.ndarray, np.ndarray]:
        key = (sym, freq, stop_basis, causal)
        if key in feature_cache:
            return feature_cache[key]
        frame = hourly_frames[sym]
        target = bars[sym][freq].index
        minutes = FREQ_MINUTES[freq]
        if freq == "60min":
            vol_ok = frame.vol_ok.reindex(target).fillna(False)
            hourly_atr = frame.atr.reindex(target)
        elif causal:
            vol_ok = map_hourly_causal(frame.vol_ok, target, minutes).fillna(False)
            hourly_atr = map_hourly_causal(frame.atr, target, minutes)
        else:
            # Reproduce the reported implementation exactly: this is
            # look-ahead for sub-hourly bars inside the current hour.
            vol_ok = frame.vol_ok.reindex(target, method="ffill").fillna(False)
            hourly_atr = frame.atr.reindex(target, method="ffill")
        atr = (strategy.atr(bars[sym][freq], 20)
               if stop_basis == "native" else hourly_atr)
        result = atr.to_numpy(), vol_ok.to_numpy(dtype=bool)
        feature_cache[key] = result
        return result

    def simulate(sym: str, freq: str, window: str, stop_basis: str,
                 prior_bars: int, causal_features: bool,
                 causal_core: bool, buffer_atr: float = 0.25,
                 trail_k: float = 2.0,
                 cutoff_hour: int = 14,
                 trail_update: str = "native") -> pd.DataFrame:
        b = bars[sym][freq]
        atr, vol_ok = features(sym, freq, stop_basis, causal_features)
        et = b.index.tz_convert("America/New_York")
        hours, dates = et.hour.to_numpy(), et.date
        eligible = (hours >= 9) & (hours < 16)
        opens, highs, lows, closes = (b[c].to_numpy()
                                      for c in ("open", "high", "low", "close"))
        rolls, ts = b.roll.to_numpy(), b.index
        roll_dates = set(dates[rolls == 1])
        last = {dates[i]: i for i in np.flatnonzero(eligible)}
        out = []
        pos = pend = 0
        entry = high_water = stop = np.nan
        entry_time = None
        current = None
        session_high, seen, done = -np.inf, 0, False
        for i in range(len(b)):
            if dates[i] != current:
                current = dates[i]
                session_high, seen, done = -np.inf, 0, False
                pos = pend = 0
            if not eligible[i]:
                continue
            if pend:
                pos, pend = 1, 0
                entry = high_water = opens[i]
                stop = entry - trail_k * atr[i - 1]
                entry_time = ts[i]
                if not np.isfinite(stop):
                    pos = 0
            if pos:
                if lows[i] <= stop:
                    price = min(stop, opens[i])
                    out.append((entry_time, ts[i], sym, entry, price, "stop"))
                    pos = 0
                elif i == last[current]:
                    out.append((entry_time, ts[i], sym, entry, closes[i], "eod"))
                    pos = 0
                else:
                    high_water = max(high_water, highs[i])
                    # Research updates the broker stop after every native bar.
                    # Production v2 currently invokes the shared trailing
                    # machinery only after an hourly bar closes.
                    close_et = et[i] + pd.Timedelta(
                        minutes=FREQ_MINUTES[freq])
                    update_now = (trail_update == "native"
                                  or close_et.minute == 0)
                    if update_now:
                        stop = max(stop, high_water - trail_k * atr[i])
            if (not pos and not pend and not done and seen >= prior_bars
                    and current not in roll_dates and benched(sym, ts[i])
                    and np.isfinite(atr[i]) and vol_ok[i]
                    and closes[i] > session_high + buffer_atr * atr[i]
                    and i + 1 < len(b) and eligible[i + 1]
                    and dates[i + 1] == current):
                entry_hour = hours[i + 1]
                floor = {"early": 0, "after10": 10,
                         "after11": 11, "noon": 12}[window]
                window_ok = floor <= entry_hour < cutoff_hour
                if window_ok:
                    entry_ts = ts[i + 1]
                    if causal_core:
                        core_qty = causal_core_qty(entry_ts)
                    else:
                        loc = naive_events.index.searchsorted(
                            entry_ts, side="right") - 1
                        core_qty = int(naive_events.iloc[loc]) if loc >= 0 else 0
                    if core_qty > 0:
                        pend, done = 1, True
            session_high = max(session_high, highs[i])
            seen += 1
        return pd.DataFrame(out, columns=["entry_time", "exit_time", "sym",
                                          "entry_px", "exit_px", "reason"])

    rows = []

    def run_cell(name: str, freq: str, window: str, stop_basis: str,
                 prior_bars: int, causal_features: bool,
                 causal_core: bool, buffer_atr: float = 0.25,
                 trail_k: float = 2.0, cutoff_hour: int = 14,
                 trail_update: str = "native",
                 family: str = "audit") -> None:
        sat = pd.concat([
            simulate(s, freq, window, stop_basis, prior_bars,
                     causal_features, causal_core, buffer_atr, trail_k,
                     cutoff_hour, trail_update)
            for s in bars], ignore_index=True)
        row = metrics(core, sat, bars60)
        sat25 = sat[(sat.exit_time >= START) & (sat.exit_time < END)].copy()
        pnl26 = sat[sat.exit_time >= END].copy()
        row["y2026"] = float((((pnl26.exit_px - pnl26.entry_px)
                               * pnl26.sym.map(rg.MULT)) - 3.5).sum())
        row["n_h1"] = int((sat25.exit_time.dt.year <= 2017).sum())
        row["n_h2"] = int((sat25.exit_time.dt.year >= 2018).sum())
        row["sat_at_7"] = row["sat"] - 3.5 * row["n"]
        row["sat_at_14"] = row["sat"] - 10.5 * row["n"]
        row["h1_at_7"] = row["h1"] - 3.5 * row["n_h1"]
        row["h2_at_7"] = row["h2"] - 3.5 * row["n_h2"]
        entry_et = sat25.entry_time.dt.tz_convert("America/New_York")
        row["entry_hour"] = {str(int(k)): int(v)
                             for k, v in entry_et.dt.hour.value_counts().sort_index().items()}
        row["entry_minute"] = {str(int(k)): int(v)
                               for k, v in entry_et.dt.minute.value_counts().sort_index().items()}
        row["symbol"] = {str(k): int(v)
                         for k, v in sat25.sym.value_counts().sort_index().items()}
        row.update({"name": name, "family": family, "freq": freq,
                    "window": window, "stop_basis": stop_basis,
                    "prior_bars": prior_bars,
                    "prior_minutes": prior_bars * FREQ_MINUTES[freq],
                    "causal_features": causal_features,
                    "causal_core": causal_core,
                    "buffer_atr": buffer_atr, "trail_k": trail_k,
                    "trail_update": trail_update})
        row["cutoff_hour"] = cutoff_hour
        rows.append(row)
        if name in {"reported_15m_early_hequiv",
                    "causal_15m_early_hequiv", "causal_60m_ref",
                    "surface_10m_prior30", "reported_deployed_cutoff15",
                    "causal_deployed_cutoff15",
                    "causal_deployed_cutoff15_hourlytrail",
                    "liveparity_15m_finalist", "liveparity_10m_finalist",
                    "liveparity_10m_k2_5_finalist"}:
            sat.to_csv(HERE / f"{name}_trades.csv", index=False)
        print(f"{name:30s} sat {row['sat']:9.2f} n {row['n']:4d} "
              f"ratio {row['ratio']:5.2f} worst {row['worst']:9.2f} "
              f"halves {row['h1']:8.2f}/{row['h2']:8.2f}", flush=True)

    if "--deployed-cutoff-only" in sys.argv:
        run_cell("reported_deployed_cutoff15", "15min", "early", "hequiv",
                 2, False, False, cutoff_hour=15,
                 family="deployed_reproduction")
        for cutoff in (12, 13, 14, 15):
            run_cell(f"causal_deployed_cutoff{cutoff}", "15min", "early",
                     "hequiv", 2, True, True, cutoff_hour=cutoff,
                     family="deployed_causal_cutoff")
        run_cell("causal_deployed_cutoff15_hourlytrail", "15min", "early",
                 "hequiv", 2, True, True, cutoff_hour=15,
                 trail_update="hourly", family="deployed_live_parity")
        frame = pd.DataFrame(rows)
        frame.to_csv(HERE / "deployed_cutoff_cells.csv", index=False)
        return

    if "--deployed-improvements-only" in sys.argv:
        # Exploratory neighbors of the corrected, production-cadence rule.
        # These are not promotion-eligible because the full history selected
        # both the baseline and this neighborhood.
        run_cell("liveparity_15m_p30_k2_b.25_c15", "15min", "early",
                 "hequiv", 2, True, True, cutoff_hour=15,
                 trail_update="hourly", family="liveparity_baseline")
        for freq in ("5min", "10min", "15min", "20min", "30min"):
            run_cell(f"liveparity_{FREQ_MINUTES[freq]}m_native2", freq,
                     "early", "hequiv", 2, True, True, cutoff_hour=15,
                     trail_update="hourly", family="liveparity_frequency")
        for freq in ("5min", "10min", "15min", "30min"):
            minutes = FREQ_MINUTES[freq]
            run_cell(f"liveparity_{minutes}m_matched30", freq, "early",
                     "hequiv", 30 // minutes, True, True, cutoff_hour=15,
                     trail_update="hourly",
                     family="liveparity_matched30")
        for trail in (1.5, 2.0, 2.5, 3.0):
            run_cell(f"liveparity_10m_k{trail:g}", "10min", "early",
                     "hequiv", 3, True, True, trail_k=trail,
                     cutoff_hour=15, trail_update="hourly",
                     family="liveparity_10m_trail")
        for buffer in (0.10, 0.25, 0.40, 0.50):
            run_cell(f"liveparity_10m_b{buffer:g}", "10min", "early",
                     "hequiv", 3, True, True, buffer_atr=buffer,
                     cutoff_hour=15, trail_update="hourly",
                     family="liveparity_10m_buffer")
        frame = pd.DataFrame(rows)
        frame.to_csv(HERE / "deployed_improvement_cells.csv", index=False)
        return

    if "--deployed-finalists-only" in sys.argv:
        for name, freq, prior, trail in (
                ("liveparity_15m_finalist", "15min", 2, 2.0),
                ("liveparity_10m_finalist", "10min", 2, 2.0),
                ("liveparity_10m_k2_5_finalist", "10min", 3, 2.5)):
            run_cell(name, freq, "early", "hequiv", prior, True, True,
                     cutoff_hour=15, trail_k=trail, trail_update="hourly",
                     family="liveparity_finalist")
        pd.DataFrame(rows).to_csv(
            HERE / "deployed_finalist_cells.csv", index=False)
        return

    # Exact reported cells (naive mapping/event ordering) and corrected twins.
    reported = [("60m_ref", "60min", "noon", "hequiv", 2)]
    for freq, tag in (("30min", "30m"), ("15min", "15m")):
        for window in ("early", "noon"):
            for stop_basis in ("native", "hequiv"):
                reported.append((f"{tag}_{window}_{stop_basis}", freq,
                                 window, stop_basis, 2))
    for name, freq, window, stop_basis, prior in reported:
        run_cell("reported_" + name, freq, window, stop_basis, prior,
                 False, False, family="reported_reproduction")
        run_cell("causal_" + name, freq, window, stop_basis, prior,
                 True, True, family="registered_causal")

    # Latency isolation: every frequency waits for the same 120 minutes of
    # completed session information before a signal may qualify.
    for freq in ("60min", "30min", "15min", "10min", "5min"):
        minutes = FREQ_MINUTES[freq]
        run_cell(f"matched120_{minutes}m_hequiv", freq, "early", "hequiv",
                 120 // minutes, True, True, family="matched_120m")

    # Disclosed exploratory follow-up: frequency and channel maturation are
    # crossed explicitly instead of changing together with native bar count.
    for prior_minutes in (30, 60, 90, 120, 150, 180):
        for freq in ("60min", "30min", "15min", "10min", "5min"):
            minutes = FREQ_MINUTES[freq]
            if prior_minutes % minutes:
                continue
            run_cell(f"surface_{minutes}m_prior{prior_minutes}", freq,
                     "early", "hequiv", prior_minutes // minutes,
                     True, True, family="frequency_maturation")

    # Local mechanism surface around 15m / two native bars.
    for buffer in (0.0, 0.10, 0.25, 0.40, 0.50):
        for trail in (1.5, 2.0, 2.5, 3.0):
            run_cell(f"surface_15m_b{buffer:g}_k{trail:g}", "15min",
                     "early", "hequiv", 2, True, True,
                     buffer_atr=buffer, trail_k=trail,
                     family="buffer_trail")

    for window in ("early", "after10", "after11", "noon"):
        run_cell(f"surface_15m_window_{window}", "15min", window,
                 "hequiv", 2, True, True,
                 family="entry_window")

    # Second disclosed exploratory follow-up.  The first causal surface made
    # 10m / 30-minute maturity its in-sample maximum, so map that neighborhood
    # without treating the resulting maximum as confirmatory evidence.
    for prior_minutes in range(20, 121, 10):
        run_cell(f"local10_prior{prior_minutes}", "10min", "early",
                 "hequiv", prior_minutes // 10, True, True,
                 family="local10_maturation")
    for buffer in (0.0, 0.10, 0.25, 0.40, 0.50):
        for trail in (1.5, 2.0, 2.5, 3.0):
            run_cell(f"local10_b{buffer:g}_k{trail:g}", "10min", "early",
                     "hequiv", 3, True, True,
                     buffer_atr=buffer, trail_k=trail,
                     family="local10_buffer_trail")
    for window in ("early", "after10", "after11", "noon"):
        run_cell(f"local10_window_{window}", "10min", window,
                 "hequiv", 3, True, True,
                 family="local10_entry_window")

    frame = pd.DataFrame(rows)
    frame.to_csv(HERE / "validation_cells.csv", index=False)
    (HERE / "validation_summary.json").write_text(json.dumps({
        "generated_utc": pd.Timestamp.utcnow().isoformat(),
        "rows": rows,
    }, indent=2))


if __name__ == "__main__":
    main()
