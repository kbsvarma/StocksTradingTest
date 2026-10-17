"""Fresh BPS trade-level engine. Zero logic imported from old backtests.

Per (day, config): gates -> strike selection at entry -> entry credit from NBBO ->
minute-by-minute SL monitoring on cost-to-close -> exit at SL or expiry settlement
vs official Cboe SPX close.

Fill model (initial, to be calibrated by live-fill reconciliation):
  entry:  sell short leg at BID, buy long leg at ASK  (conservative cross)
  SL exit: buy short leg at ASK, sell long leg at BID (conservative cross)
Commissions: $per_leg_rt applied per spread round trip (2 legs).

Data honesty rules:
  - quote staleness: last-known quote used up to MAX_STALE_MIN minutes; beyond that
    the minute is 'blind' — SL cannot fire, and blind-minute count is recorded.
  - if entry-time quotes are missing for the selected strikes -> no trade, logged.
  - if a day lacks chain data entirely -> excluded, logged.
"""
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd

from chain import load_day, parity_spot

ET = "America/New_York"
MAX_STALE_MIN = 5

@dataclass
class Config:
    entry_hh: int
    entry_mm: int
    width: float
    otm_pct: float          # e.g. 0.010
    dir_filter: str         # 'none' | 'spx_ge_open' | 'spx_ge_open_25bp' | 'spx_and_es'
    sl_mult: float          # 0 = no SL
    vix_gate: bool          # live gate (12-25) on/off
    min_credit: float = 0.05  # ignore quotes thinner than this

    def label(self):
        return (f"T{self.entry_hh:02d}{self.entry_mm:02d}_W{int(self.width)}"
                f"_O{self.otm_pct*1000:.1f}_D{self.dir_filter}_SL{self.sl_mult}_V{int(self.vix_gate)}")

def round5(x):
    return round(x / 5.0) * 5.0

def build_minute_grid(side_df):
    """pivot: index=ET minute, columns=strike, values=(bid, ask). Returns two frames."""
    bid = side_df.pivot_table(index="minute", columns="strike", values="bid_px_00", aggfunc="first")
    ask = side_df.pivot_table(index="minute", columns="strike", values="ask_px_00", aggfunc="first")
    # forward-fill with staleness cap
    bid = bid.ffill(limit=MAX_STALE_MIN)
    ask = ask.ffill(limit=MAX_STALE_MIN)
    return bid, ask

class DayContext:
    """Everything shared across configs for one trading day (loaded once)."""
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
        d = pd.Timestamp(tag).tz_localize(ET)
        self.minutes = self.pbid.index
        self.day = day
        self.d0 = d
        # spot anchors
        self.spot_open, n_open = parity_spot(day, d + pd.Timedelta(hours=9, minutes=32))
        if np.isnan(self.spot_open):
            self.spot_open, n_open = parity_spot(day, d + pd.Timedelta(hours=9, minutes=35))
        if np.isnan(self.spot_open):
            self.reason = "no parity spot at open"
            return
        self.ok = True
        self.reason = ""
        self._spot_cache = {}

    def spot_at(self, t):
        if t not in self._spot_cache:
            s, n = parity_spot(self.day, t)
            self._spot_cache[t] = s
        return self._spot_cache[t]

def sim_day(ctx: DayContext, cfg: Config, commission_rt=2.60, entry_slip=0.0, exit_slip=0.0):
    """Simulate one config on one day. Returns dict or None (no trade + reason)."""
    if not ctx.ok:
        return {"tag": ctx.tag, "status": "no-data", "reason": ctx.reason}
    # VIX gate (daily open; intraday rise not testable -> reported limitation)
    if cfg.vix_gate:
        if not (12.0 <= ctx.vix_open <= 25.0):
            return {"tag": ctx.tag, "status": "gate-vix", "vix": ctx.vix_open}
    t_entry = ctx.d0 + pd.Timedelta(hours=cfg.entry_hh, minutes=cfg.entry_mm)
    if t_entry not in ctx.minutes:
        return {"tag": ctx.tag, "status": "no-entry-minute"}
    spot = ctx.spot_at(t_entry)
    if np.isnan(spot):
        return {"tag": ctx.tag, "status": "no-spot-at-entry"}
    # direction filter
    if cfg.dir_filter == "spx_ge_open" and not (spot >= ctx.spot_open):
        return {"tag": ctx.tag, "status": "gate-dir"}
    if cfg.dir_filter == "spx_ge_open_25bp" and not (spot >= ctx.spot_open * 1.0025):
        return {"tag": ctx.tag, "status": "gate-dir"}
    # LIVE strike-selection rule (webull_bot/market_data.py): among the 5 strikes
    # nearest round5(spot*(1-otm)), pick the candidate (with long = nearest
    # available <= short-width, >=0.8*width) whose MID credit is closest to
    # target_credit ($2.00), subject to mid >= min_credit.
    target = round5(spot * (1 - cfg.otm_pct))
    avail = [k for k in ctx.pbid.columns if not np.isnan(ctx.pbid.at[t_entry, k]) or True]
    cand = sorted(ctx.pbid.columns, key=lambda s: abs(s - target))[:5]
    best = None; best_diff = np.inf
    for s_k in cand:
        below = [k for k in ctx.pbid.columns if k <= s_k - cfg.width]
        if not below:
            continue
        l_k = max(below)
        if s_k - l_k < cfg.width * 0.8:
            continue
        sb_ = ctx.pbid.at[t_entry, s_k]; sa_ = ctx.pask.at[t_entry, s_k]
        lb_ = ctx.pbid.at[t_entry, l_k]; la_ = ctx.pask.at[t_entry, l_k]
        if any(np.isnan(x) for x in (sb_, sa_, lb_, la_)) or sb_ <= 0 or la_ <= 0:
            continue
        mid = (sb_ + sa_) / 2 - (lb_ + la_) / 2
        if mid < cfg.min_credit:
            continue
        diff = abs(mid - 2.0)
        if diff < best_diff:
            best_diff = diff
            best = (s_k, l_k, sb_, sa_, lb_, la_)
    if best is None:
        return {"tag": ctx.tag, "status": "no-spread-above-floor"}
    ks, kl, sb, sa, lb, la = best
    credit = (sb - la) - entry_slip
    if credit < 0.05:
        return {"tag": ctx.tag, "status": "credit-too-thin", "credit": credit}
    # SL monitoring from entry+1min to last minute
    after = ctx.minutes[ctx.minutes > t_entry]
    blind = 0
    sl_time = None
    exit_cost = None
    if cfg.sl_mult > 0:
        ask_s = ctx.pask.loc[after, ks]
        bid_l = ctx.pbid.loc[after, kl]
        cost = ask_s - bid_l + exit_slip
        blind = int((ask_s.isna() | bid_l.isna()).sum())
        trig = cost[(~cost.isna()) & (cost >= cfg.sl_mult * credit)]
        if len(trig) > 0:
            sl_time = trig.index[0]
            exit_cost = float(trig.iloc[0])
    if sl_time is not None:
        pnl_pts = credit - exit_cost
        status = "stop"
    else:
        intrinsic = max(0.0, ks - ctx.spx_close) - max(0.0, kl - ctx.spx_close)
        pnl_pts = credit - intrinsic
        status = "expire"
    pnl_usd = pnl_pts * 100.0 - commission_rt * (2 if sl_time is not None else 1)
    return {"tag": ctx.tag, "status": status, "credit": credit, "short": ks, "long": kl,
            "pnl_usd": pnl_usd, "sl_time": str(sl_time) if sl_time is not None else "",
            "blind_minutes": blind, "spot_entry": spot, "vix": ctx.vix_open}
