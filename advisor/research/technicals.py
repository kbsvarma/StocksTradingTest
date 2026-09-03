"""Deterministic multi-factor technical state engine.

This module describes *market state*; it does not claim predictive alpha.
Every metric is computed from the immutable, quality-gated OHLCV panel and
uses information available at the as-of close.  The resulting setups widen
the research funnel while the existing red-team/actionability gates remain
in force.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR, current_meta, load_panel

ET = ZoneInfo("America/New_York")
OUT = RESEARCH_DIR / "technical_latest.json"
PROFILES_OUT = RESEARCH_DIR / "technical_profiles_latest.json"


def _last(frame):
    return frame.iloc[-1]


def compute_frame(close, high, low, volume, universe: dict):
    """Return one row per stock with interpretable technical diagnostics."""
    import numpy as np
    import pandas as pd

    stocks = universe["stocks"]
    cols = [c for c in close.columns if c in stocks]
    c, h, l, v = close[cols], high[cols], low[cols], volume[cols]
    enough = c.notna().sum() >= 210
    cols = list(enough[enough].index)
    if not cols:
        return pd.DataFrame()
    c, h, l, v = c[cols], h[cols], l[cols], v[cols]
    ret = c.pct_change(fill_method=None)

    sma20, sma50, sma200 = c.rolling(20).mean(), c.rolling(50).mean(), c.rolling(200).mean()
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_hist = macd - macd.ewm(span=9, adjust=False).mean()

    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.mask((loss == 0) & (gain > 0), 100.0)
    rsi = rsi.mask((gain == 0) & (loss > 0), 0.0)

    prev = c.shift(1)
    tr = pd.DataFrame(np.maximum.reduce([
        (h - l).to_numpy(), (h - prev).abs().to_numpy(),
        (l - prev).abs().to_numpy(),
    ]), index=c.index, columns=c.columns)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    up, down = h.diff(), -l.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr_safe = atr.replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr_safe
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr_safe
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / 14, adjust=False).mean()

    std20 = c.rolling(20).std().replace(0, np.nan)
    hi55, lo55 = c.rolling(55).max(), c.rolling(55).min()
    prior_hi55, prior_lo55 = hi55.shift(1), lo55.shift(1)
    dollar_vol = (c * v).rolling(21, min_periods=10).median()
    spy63 = 0.0
    if "SPY" in close and len(close["SPY"].dropna()) >= 64:
        spy63 = float(close["SPY"].iloc[-1] / close["SPY"].iloc[-64] - 1)

    out = pd.DataFrame(index=cols)
    px = _last(c)
    out["px"] = px
    out["rsi14"] = _last(rsi)
    out["adx14"] = _last(adx)
    out["atr14_pct"] = _last(atr) / px * 100
    out["macd_hist_pct"] = _last(macd_hist) / px * 100
    out["bollinger_z"] = (_last(c) - _last(sma20)) / (2 * _last(std20))
    out["donchian55"] = (_last(c) - _last(lo55)) / (_last(hi55) - _last(lo55)).replace(0, np.nan)
    out["ma20_gap_pct"] = (px / _last(sma20) - 1) * 100
    out["ma50_gap_pct"] = (px / _last(sma50) - 1) * 100
    out["ma200_gap_pct"] = (px / _last(sma200) - 1) * 100
    out["trend_stack"] = ((_last(c) > _last(sma20)).astype(int)
                           + (_last(sma20) > _last(sma50)).astype(int)
                           + (_last(sma50) > _last(sma200)).astype(int))
    out["ret_21d_pct"] = (px / c.iloc[-22] - 1) * 100
    out["ret_63d_pct"] = (px / c.iloc[-64] - 1) * 100
    out["rs_spy_63d_pct"] = out["ret_63d_pct"] - spy63 * 100
    out["volume_ratio_20_120"] = (_last(v.rolling(20).mean())
                                   / _last(v.rolling(120).mean()).replace(0, np.nan))
    out["downside_vol60_ann_pct"] = (ret.where(ret < 0).rolling(60).std().iloc[-1]
                                      * np.sqrt(252) * 100)
    out["drawdown126_pct"] = (px / _last(c.rolling(126).max()) - 1) * 100
    out["dollar_vol_21d_m"] = _last(dollar_vol) / 1e6
    out["breakout55"] = (px >= _last(prior_hi55) * 0.995) & (out["volume_ratio_20_120"] >= 1.15)
    out["breakdown55"] = (px <= _last(prior_lo55) * 1.005) & (out["volume_ratio_20_120"] >= 1.15)

    strong_up = ((out.trend_stack == 3) & (out.adx14 >= 20)
                 & out.rsi14.between(45, 75) & (out.rs_spy_63d_pct > 0))
    pullback = ((out.ma200_gap_pct > 0) & (out.ma50_gap_pct > -3)
                & out.rsi14.between(35, 55) & (out.bollinger_z < 0))
    strong_down = ((out.trend_stack == 0) & (out.adx14 >= 20)
                   & out.rsi14.between(25, 55) & (out.rs_spy_63d_pct < 0))
    out["setup"] = "mixed"
    out.loc[strong_up, "setup"] = "confirmed_uptrend"
    out.loc[pullback, "setup"] = "uptrend_pullback"
    out.loc[strong_down, "setup"] = "confirmed_downtrend"
    out.loc[out.breakout55.fillna(False), "setup"] = "volume_breakout"
    out.loc[out.breakdown55.fillna(False), "setup"] = "volume_breakdown"

    completeness = out.notna().mean(axis=1)
    alignment = ((out.trend_stack - 1.5).abs() / 1.5).clip(0, 1)
    strength = ((out.adx14 - 15) / 25).clip(0, 1).fillna(0)
    out["state_confidence"] = (100 * completeness * (0.55 + 0.25 * alignment
                                                       + 0.20 * strength)).clip(0, 100)
    out["sector"] = [stocks.get(t, "?") for t in out.index]
    return out.replace([np.inf, -np.inf], np.nan)


def build(top: int = 25) -> dict:
    from advisor.research.universe import load as load_universe

    frame = compute_frame(load_panel("close"), load_panel("high"), load_panel("low"),
                          load_panel("volume"), load_universe())
    liquid = frame[(frame.dollar_vol_21d_m >= 10) & (frame.px >= 5)].copy()

    def records(part):
        rows = []
        for ticker, row in part.iterrows():
            values = {}
            for k in part.columns:
                value = row[k]
                if value != value:
                    value = None
                elif hasattr(value, "item"):
                    value = value.item()
                values[k] = value
            rows.append({"ticker": ticker, **{
                k: (round(float(v), 3) if isinstance(v, (float, int)) and not isinstance(v, bool) else v)
                for k, v in values.items()}})
        return rows

    actionable_setups = liquid[liquid.setup != "mixed"].sort_values(
        ["state_confidence", "dollar_vol_21d_m"], ascending=False)
    result = {
        "schema_version": 1,
        "as_of": datetime.now(ET).isoformat(),
        "panel_build_id": current_meta().get("build_id", "legacy"),
        "n_profiled": len(frame), "n_liquid": len(liquid),
        "gate": "DESCRIPTIVE/RESEARCH-ONLY — setup labels are not calibrated probabilities or alpha",
        "method": ("RSI14, MACD(12,26,9), ADX14, ATR14, Bollinger20, Donchian55, "
                   "20/50/200 trend stack, SPY-relative strength, volume confirmation, "
                   "downside volatility and drawdown; as-of-close, next-session use"),
        "setups": records(actionable_setups.head(top)),
        "profiles": records(liquid.sort_values("dollar_vol_21d_m", ascending=False)),
    }
    return result


def write(result: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    profiles = result.get("profiles", [])
    profile_doc = {"schema_version": result.get("schema_version", 1),
                   "as_of": result.get("as_of"),
                   "panel_build_id": result.get("panel_build_id"),
                   "gate": result.get("gate"), "profiles": profiles}
    profiles_tmp = PROFILES_OUT.with_suffix(f".json.tmp.{os.getpid()}")
    profiles_tmp.write_text(json.dumps(profile_doc, indent=2, default=str) + "\n")
    os.replace(profiles_tmp, PROFILES_OUT)
    compact = {k: v for k, v in result.items() if k != "profiles"}
    tmp = OUT.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(compact, indent=2, default=str) + "\n")
    os.replace(tmp, OUT)


def main() -> int:
    result = build()
    write(result)
    print(f"[technicals] {result['n_profiled']} profiled; {len(result['setups'])} setups -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
