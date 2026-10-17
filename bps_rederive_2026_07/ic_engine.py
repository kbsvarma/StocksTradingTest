"""Iron Condor extension of the ACCEPTED BPS engine (PREREG_IC_FLAT).

Put-leg logic is copied byte-for-byte from engine.sim_day so the validated
behaviour is preserved; the call leg mirrors it symmetrically. Same fill
model (conservative NBBO cross), same staleness cap, same Cboe-close
settlement. Commission defaults to 4 legs.
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd

from chain import load_day, parity_spot
from engine import build_minute_grid, round5, ET, MAX_STALE_MIN  # noqa: F401


@dataclass
class ICConfig:
    entry_hh: int
    entry_mm: int
    width: float
    otm_pct: float
    sl_mult: float           # 0 = no stop
    vix_gate: bool = False   # conditioning is applied by the driver, not here
    min_credit: float = 0.05

    def label(self):
        return (f"T{self.entry_hh:02d}{self.entry_mm:02d}_W{int(self.width)}"
                f"_O{self.otm_pct*1000:.1f}_SL{self.sl_mult}")


class ICDayContext:
    """One trading day, both sides of the chain, loaded once."""

    def __init__(self, tag, vix_row, spx_close):
        self.tag = tag
        self.ok = False
        self.vix_open = vix_row["open"] if vix_row is not None else np.nan
        self.spx_close = spx_close
        day = load_day(tag)
        if day is None:
            self.reason = "no chain data"
            return
        self.pbid, self.pask = build_minute_grid(day["P"])
        self.cbid, self.cask = build_minute_grid(day["C"])
        d = pd.Timestamp(tag).tz_localize(ET)
        self.minutes = self.pbid.index.intersection(self.cbid.index)
        if len(self.minutes) < 60:
            self.reason = "too few shared minutes"
            return
        self.day = day
        self.d0 = d
        self.spot_open, _ = parity_spot(day, d + pd.Timedelta(hours=9, minutes=32))
        if np.isnan(self.spot_open):
            self.spot_open, _ = parity_spot(day, d + pd.Timedelta(hours=9, minutes=35))
        if np.isnan(self.spot_open):
            self.reason = "no parity spot at open"
            return
        self.ok = True
        self.reason = ""
        self._spot_cache = {}

    def spot_at(self, t):
        if t not in self._spot_cache:
            s, _ = parity_spot(self.day, t)
            self._spot_cache[t] = s
        return self._spot_cache[t]


def _pick_put(ctx, t, spot, cfg):
    """EXACT put rule from engine.sim_day (credit-target strike selection)."""
    target = round5(spot * (1 - cfg.otm_pct))
    cand = sorted(ctx.pbid.columns, key=lambda s: abs(s - target))[:5]
    best, best_diff = None, np.inf
    for s_k in cand:
        below = [k for k in ctx.pbid.columns if k <= s_k - cfg.width]
        if not below:
            continue
        l_k = max(below)
        if s_k - l_k < cfg.width * 0.8:
            continue
        sb_, sa_ = ctx.pbid.at[t, s_k], ctx.pask.at[t, s_k]
        lb_, la_ = ctx.pbid.at[t, l_k], ctx.pask.at[t, l_k]
        if any(np.isnan(x) for x in (sb_, sa_, lb_, la_)) or sb_ <= 0 or la_ <= 0:
            continue
        mid = (sb_ + sa_) / 2 - (lb_ + la_) / 2
        if mid < cfg.min_credit:
            continue
        diff = abs(mid - 2.0)
        if diff < best_diff:
            best_diff, best = diff, (s_k, l_k, sb_, la_)
    return best


def _pick_call(ctx, t, spot, cfg):
    """Mirror of the put rule, above spot."""
    target = round5(spot * (1 + cfg.otm_pct))
    cand = sorted(ctx.cbid.columns, key=lambda s: abs(s - target))[:5]
    best, best_diff = None, np.inf
    for s_k in cand:
        above = [k for k in ctx.cbid.columns if k >= s_k + cfg.width]
        if not above:
            continue
        l_k = min(above)
        if l_k - s_k < cfg.width * 0.8:
            continue
        sb_, sa_ = ctx.cbid.at[t, s_k], ctx.cask.at[t, s_k]
        lb_, la_ = ctx.cbid.at[t, l_k], ctx.cask.at[t, l_k]
        if any(np.isnan(x) for x in (sb_, sa_, lb_, la_)) or sb_ <= 0 or la_ <= 0:
            continue
        mid = (sb_ + sa_) / 2 - (lb_ + la_) / 2
        if mid < cfg.min_credit:
            continue
        diff = abs(mid - 2.0)
        if diff < best_diff:
            best_diff, best = diff, (s_k, l_k, sb_, la_)
    return best


def sim_day_ic(ctx: ICDayContext, cfg: ICConfig, commission_rt=5.20,
               entry_slip=0.0, exit_slip=0.0):
    if not ctx.ok:
        return {"tag": ctx.tag, "status": "no-data", "reason": ctx.reason}
    t = ctx.d0 + pd.Timedelta(hours=cfg.entry_hh, minutes=cfg.entry_mm)
    if t not in ctx.minutes:
        return {"tag": ctx.tag, "status": "no-entry-minute"}
    spot = ctx.spot_at(t)
    if np.isnan(spot):
        return {"tag": ctx.tag, "status": "no-spot-at-entry"}
    p = _pick_put(ctx, t, spot, cfg)
    c = _pick_call(ctx, t, spot, cfg)
    if p is None or c is None:
        return {"tag": ctx.tag, "status": "no-spread-above-floor"}
    kps, kpl, pb, pla = p
    kcs, kcl, cb, cla = c
    cr_put = pb - pla
    cr_call = cb - cla
    credit = cr_put + cr_call - entry_slip
    if credit < 0.10:
        return {"tag": ctx.tag, "status": "credit-too-thin", "credit": credit}
    after = ctx.minutes[ctx.minutes > t]
    blind = 0
    sl_time = exit_cost = None
    if cfg.sl_mult > 0:
        cost_p = ctx.pask.loc[after, kps] - ctx.pbid.loc[after, kpl]
        cost_c = ctx.cask.loc[after, kcs] - ctx.cbid.loc[after, kcl]
        cost = cost_p + cost_c + exit_slip
        blind = int(cost.isna().sum())
        trig = cost[(~cost.isna()) & (cost >= cfg.sl_mult * credit)]
        if len(trig) > 0:
            sl_time = trig.index[0]
            exit_cost = float(trig.iloc[0])
    if sl_time is not None:
        pnl_pts = credit - exit_cost
        status = "stop"
    else:
        S = ctx.spx_close
        intr_p = max(0.0, kps - S) - max(0.0, kpl - S)
        intr_c = max(0.0, S - kcs) - max(0.0, S - kcl)
        pnl_pts = credit - intr_p - intr_c
        status = "expire"
    pnl_usd = pnl_pts * 100.0 - commission_rt * (2 if sl_time is not None else 1)
    # which side was threatened (whipsaw diagnostic)
    touch_p = bool(ctx.spx_close < kps) if sl_time is None else None
    return {"tag": ctx.tag, "status": status, "credit": credit,
            "credit_put": cr_put, "credit_call": cr_call,
            "kps": kps, "kpl": kpl, "kcs": kcs, "kcl": kcl,
            "pnl_usd": pnl_usd, "sl_time": str(sl_time) if sl_time is not None else "",
            "blind_minutes": blind, "spot_entry": spot, "vix": ctx.vix_open,
            "spx_close": ctx.spx_close, "put_itm_at_close": touch_p}
