"""Iteration-2 episode mechanics defined in EPISODE_PROTOCOL.md."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(ROOT / "trend_bot"),
                str(ROOT / "trend_bot" / "research")]

import rotation_grid as rg  # noqa: E402
import strategy  # noqa: E402
from rotation import monday_of_week  # noqa: E402
from run_policy_search import START, hourly_mtm, weekly_policy  # noqa: E402


VARIANTS = {
    "baseline": {},
    "cooldown_1": {"cooldown": 1},
    "cooldown_3": {"cooldown": 3},
    "cooldown_6": {"cooldown": 6},
    "fresh_breakout": {"fresh": True},
    "pyr_2+1_0.5ATR": {"stages": ((2, 0.0), (1, 0.5))},
    "pyr_2+1_1.0ATR": {"stages": ((2, 0.0), (1, 1.0))},
    "pyr_1+2_0.5ATR": {"stages": ((1, 0.0), (2, 0.5))},
    "pyr_1+2_1.0ATR": {"stages": ((1, 0.0), (2, 1.0))},
    "pyr_1+1+1": {"stages": ((1, 0.0), (1, 0.5), (1, 1.0))},
}


def simulate(B: pd.DataFrame, sym: str, policy: pd.DataFrame, *,
             cooldown: int = 0, fresh: bool = False,
             stages: tuple[tuple[int, float], ...] | None = None,
             session_rearm: bool = False,
             stage_execution: str = "high_touch",
             risk_budget_atr_contracts: float | None = None,
             add_slippage_points: float = 0.0,
             liquidate_end: bool = False,
             prior_high_rearm: bool = False,
             loss_session_rearm: bool = False,
             loss_same_bar_rearm: bool = False,
             concentrated_loss_session_rearm: bool = False) -> pd.DataFrame:
    if stage_execution not in {
            "high_touch", "close_next_open", "high_next_open"}:
        raise ValueError(f"unsupported stage_execution={stage_execution!r}")
    if risk_budget_atr_contracts is not None and stage_execution != "high_next_open":
        raise ValueError("risk budget requires high_next_open execution")
    f = strategy.signal_frame(B, rg.SCFG)
    atr, signal = f["atr"].values, f["signal"].values
    O, H, L, C = (B[c].values for c in ("open", "high", "low", "close"))
    R, ts = B["roll"].values, B.index
    rows = []
    units = []
    pending_qty = 0
    high_water = stop = signal_atr = np.nan
    episode = 0
    cooldown_through = -1
    blocked_session = None
    rearm_level = np.nan
    armed = True
    next_stage = 0
    pending_add_qty = 0
    plan = stages

    def cme_session(stamp):
        """Session date for the 18:00 ET futures-session boundary."""
        et = stamp.tz_convert("America/New_York")
        day = et.normalize()
        return day + pd.Timedelta(days=1) if et.hour >= 18 else day

    def target(stamp):
        mon = monday_of_week(stamp)
        return int(policy.loc[mon, f"L_{sym}"]) if mon in policy.index else 1

    def append_exit(exit_time, exit_px, reason):
        nonlocal units, pending_add_qty
        for entry_time, entry_px, quantity in units:
            rows.append((entry_time, exit_time, sym, entry_px, exit_px,
                         exit_px - entry_px, reason, 1, quantity, episode))
        units = []
        pending_add_qty = 0

    for i in range(len(B)):
        if R[i] and not units:
            # A continuous-series splice invalidates a price level recorded on
            # the prior contract.
            rearm_level = np.nan
        if not signal[i]:
            armed = True
        if pending_qty and R[i]:
            pending_qty = 0
        if pending_add_qty and (R[i] or not units):
            pending_add_qty = 0
        if pending_qty:
            episode += 1
            signal_atr = atr[i - 1]
            q0 = pending_qty if plan is None else min(plan[0][0], pending_qty)
            units = [(ts[i], O[i], q0)]
            pending_qty = 0
            high_water = O[i]
            stop = O[i] - rg.SCFG["k_trail"] * signal_atr
            next_stage = 1
            if np.isnan(stop):
                units = []
        if pending_add_qty and units:
            max_qty = target(units[0][0])
            fill_px = O[i] + add_slippage_points
            add_qty = min(pending_add_qty,
                          max_qty - sum(u[2] for u in units))
            if fill_px <= stop:
                add_qty = 0
            if add_qty > 0 and risk_budget_atr_contracts is not None:
                budget = risk_budget_atr_contracts * signal_atr
                used = sum(q * max(px - stop, 0.0)
                           for _, px, q in units)
                per_added = max(fill_px - stop, 0.0)
                capacity = max(budget - used, 0.0)
                allowed = int(np.floor(capacity / per_added + 1e-12)) \
                    if per_added > 0 else add_qty
                add_qty = min(add_qty, allowed)
            if add_qty > 0:
                # A close-confirmed stage is a market-at-next-open model.  It
                # deliberately accepts gaps and can be stopped later in this
                # same hourly bar; it does not award the intrabar threshold.
                units.append((ts[i], fill_px, add_qty))
                pending_add_qty -= add_qty
        if units and R[i]:
            append_exit(ts[i], C[i - 1] if i else O[i], "roll")
            cooldown_through = -1
            blocked_session = None
        if units:
            if L[i] <= stop:
                stopped_high_water = high_water
                exit_px = min(stop, O[i])
                episode_qty = sum(quantity for _, _, quantity in units)
                losing_episode = sum(
                    (exit_px - entry_px) * quantity
                    for _, entry_px, quantity in units) <= 0
                append_exit(ts[i], exit_px, "stop")
                # Baseline may re-enter from the stop bar's completed close.
                # A requested n-bar cooldown suppresses that bar plus n-1
                # subsequent completed bars.
                cooldown_through = i + cooldown - 1 if cooldown else -1
                if loss_same_bar_rearm and losing_episode:
                    cooldown_through = max(cooldown_through, i)
                if (session_rearm or (loss_session_rearm and losing_episode)
                        or (concentrated_loss_session_rearm and losing_episode
                            and episode_qty > 1)):
                    blocked_session = cme_session(ts[i])
                if prior_high_rearm:
                    rearm_level = stopped_high_water
            else:
                if plan is not None and stage_execution == "high_touch":
                    max_qty = target(units[0][0])
                    while next_stage < len(plan) and sum(u[2] for u in units) < max_qty:
                        add_qty, atr_level = plan[next_stage]
                        level = units[0][1] + atr_level * signal_atr
                        if H[i] < level:
                            break
                        add_qty = min(add_qty, max_qty - sum(u[2] for u in units))
                        if add_qty > 0:
                            units.append((ts[i], max(level, O[i])
                                          + add_slippage_points, add_qty))
                        next_stage += 1
                elif (plan is not None and stage_execution == "close_next_open"
                      and next_stage < len(plan)):
                    max_qty = target(units[0][0])
                    add_qty, atr_level = plan[next_stage]
                    level = units[0][1] + atr_level * signal_atr
                    if (sum(u[2] for u in units) < max_qty
                            and C[i] >= level):
                        pending_add_qty = min(
                            add_qty, max_qty - sum(u[2] for u in units))
                        next_stage += 1
                high_water = max(high_water, H[i])
                stop = max(stop, high_water - rg.SCFG["k_trail"] * atr[i])
                if (plan is not None and stage_execution == "high_next_open"
                        and pending_add_qty == 0 and next_stage < len(plan)):
                    max_qty = target(units[0][0])
                    add_qty, atr_level = plan[next_stage]
                    level = units[0][1] + atr_level * signal_atr
                    if (sum(u[2] for u in units) < max_qty
                            and H[i] >= level):
                        pending_add_qty = min(
                            add_qty, max_qty - sum(u[2] for u in units))
                        next_stage += 1
        if (not units and not pending_qty and i > rg.SCFG["donchian_n"]
                and signal[i] and i > cooldown_through
                and (not (session_rearm or loss_session_rearm
                          or concentrated_loss_session_rearm)
                     or cme_session(ts[i]) != blocked_session)
                and (not prior_high_rearm or np.isnan(rearm_level)
                     or C[i] > rearm_level)
                and (not fresh or armed)):
            q = target(ts[i])
            if q > 0:
                pending_qty = q
                if prior_high_rearm:
                    rearm_level = np.nan
                if fresh:
                    armed = False
    if liquidate_end and units:
        append_exit(ts[-1], C[-1], "data_end")
    T = pd.DataFrame(rows, columns=[
        "entry_time", "exit_time", "sym", "entry_px", "exit_px", "points",
        "reason", "dir", "qty", "episode",
    ])
    return T[T["entry_time"] >= START].reset_index(drop=True)


def summarize(T, bars, friction):
    p = hourly_mtm(T, bars, friction)
    eq = p.cumsum()
    # Cash starts at zero.  Without the clip, an initially losing path treats
    # its first negative value as a peak and understates drawdown.
    dd = eq - eq.cummax().clip(lower=0.0)
    daily = p.groupby(p.index.normalize()).sum()
    yearly = p.groupby(p.index.year).sum()
    sd = daily.std(ddof=1)
    episodes = T[["sym", "episode"]].drop_duplicates()
    return {
        "total": p.sum(), "dd": dd.min(),
        "sharpe": daily.mean() / sd * np.sqrt(252) if sd > 0 else np.nan,
        "worst": yearly.min(), "neg": int((yearly < 0).sum()),
        "a": p[p.index.year <= 2015].sum(),
        "b": p[(p.index.year >= 2016) & (p.index.year <= 2020)].sum(),
        "c": p[p.index.year >= 2021].sum(),
        "ex22": p[p.index.year != 2022].sum(),
        "y22": yearly.get(2022, 0), "episodes": len(episodes),
        "contracts": int(T["qty"].sum()),
    }


def run():
    rg.START = START
    bars = {s: rg.load_60m(s) for s in rg.MULT}
    daily = {s: B["close"].resample("1D").last().dropna()
             for s, B in bars.items()}
    policies = weekly_policy(daily, 35,
                             min(B.index.min() for B in bars.values()),
                             max(B.index.max() for B in bars.values()))
    policy = policies["baseline"]
    results = {}
    for name, kw in VARIANTS.items():
        pieces = [simulate(B, sym, policy, **kw) for sym, B in bars.items()]
        results[name] = pd.concat(pieces).sort_values("exit_time").reset_index(drop=True)

    # Unit rows are consolidated before comparing the all-at-once baseline.
    for sym, B in bars.items():
        T = results["baseline"].query("sym == @sym")
        ref = rg.simulate_weighted(
            B, sym, lambda stamp, s=sym: int(policy.loc[monday_of_week(stamp), f"L_{s}"])
            if monday_of_week(stamp) in policy.index else 1)
        a = T[["entry_time", "exit_time", "entry_px", "exit_px", "points",
               "reason", "qty"]].reset_index(drop=True)
        b = ref[["entry_time", "exit_time", "entry_px", "exit_px", "points",
                 "reason", "qty"]].reset_index(drop=True)
        if not a.equals(b):
            raise AssertionError(f"{sym}: episode baseline parity failure")

    print("Episode mechanics screen; hourly MTM; baseline parity PASS")
    print(f"{'variant':<22}{'total':>10}{'maxDD':>10}{'Sharpe':>9}"
          f"{'worst':>10}{'negY':>6}{'11-15':>10}{'16-20':>10}"
          f"{'21-end':>10}{'ex22':>10}{'2022':>9}{'eps':>7}{'ct':>8}")
    for name, T in results.items():
        s = summarize(T, bars, rg.FR)
        print(f"{name:<22}{s['total']:>10,.0f}{s['dd']:>10,.0f}"
              f"{s['sharpe']:>9.2f}{s['worst']:>10,.0f}{s['neg']:>6}"
              f"{s['a']:>10,.0f}{s['b']:>10,.0f}{s['c']:>10,.0f}"
              f"{s['ex22']:>10,.0f}{s['y22']:>9,.0f}{s['episodes']:>7}"
              f"{s['contracts']:>8}")
    print("\nCost stress (total / maxDD):")
    for name, T in results.items():
        cells = []
        for fr in (3.5, 7.0, 14.0):
            s = summarize(T, bars, fr)
            cells.append(f"${fr:g}: {s['total']:,.0f}/{s['dd']:,.0f}")
        print(f"  {name:<20} " + " | ".join(cells))
    return results, bars


if __name__ == "__main__":
    run()
