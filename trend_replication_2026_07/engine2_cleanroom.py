"""CLEAN-ROOM re-implementation of the final locked config, written from the
spec (not from trend_test.py/filters_lab.py code):
  - 60m bars (resample label=left) from 1-min
  - Donchian signal: bar close > max(prior 20 bar closes)
  - gates at signal bar: ATR%%(bar) <= trailing-1yr 95th pct; ET start-hour not in [2,6)
  - enter next bar OPEN; initial stop = entry - 2.0*ATR20(signal bar)
  - each bar in position (incl. entry bar): first roll-force-close (exit prev close),
    then stop check (exit min(stop, open) for longs), then trail update:
    best=max(best,high); stop=max(stop, best - 2.0*ATR20(bar))
  - long only; one position; friction $3.50/RT
Implementation style deliberately different: vectorized precomputation + explicit
FSM with named states, separate from the original loop code.
"""
import numpy as np
import pandas as pd

def make_bars(min_df):
    g = min_df.resample("60min", label="left")
    B = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(),
        "low": g["low"].min(), "close": g["close"].last(),
        "roll": g["roll_flag"].max(),
    }).dropna(subset=["open", "high", "low", "close"])
    B["roll"] = B["roll"].fillna(0).astype(int)
    return B

def true_range(B):
    pc = B["close"].shift(1)
    return pd.concat([B["high"] - B["low"], (B["high"] - pc).abs(), (B["low"] - pc).abs()], axis=1).max(axis=1)

def simulate(min_df, mult, N=20, K=2.0, q=0.95, sess_block=(2, 6), friction=3.50):
    B = make_bars(min_df)
    atr = true_range(B).rolling(20).mean()
    donch = B["close"].rolling(N).max().shift(1)
    atr_pct = atr / B["close"]
    gate_thresh = atr_pct.rolling(24 * 365, min_periods=24 * 60).quantile(q)
    et_hour = B.index.tz_convert("America/New_York").hour
    signal = (B["close"] > donch) \
        & ~(atr_pct > gate_thresh) \
        & ~((et_hour >= sess_block[0]) & (et_hour < sess_block[1])) \
        & (np.arange(len(B)) > N)

    O = B["open"].values; H = B["high"].values; L = B["low"].values; C = B["close"].values
    R = B["roll"].values; SIG = signal.values; ATR = atr.values
    ts = B.index

    STATE_FLAT, STATE_PENDING, STATE_LONG = 0, 1, 2
    state = STATE_FLAT
    e_px = hi_water = stop_px = np.nan
    sig_atr = np.nan
    out = []

    for i in range(len(B)):
        if state == STATE_PENDING:
            e_px = O[i]; hi_water = O[i]
            stop_px = e_px - K * sig_atr
            state = STATE_LONG if not np.isnan(stop_px) else STATE_FLAT
        if state == STATE_LONG and R[i] == 1:
            x = C[i - 1] if i > 0 else O[i]
            out.append((ts[i], e_px, x, (x - e_px) * mult - friction, "roll"))
            state = STATE_FLAT
        if state == STATE_LONG:
            if L[i] <= stop_px:
                x = min(stop_px, O[i])
                out.append((ts[i], e_px, x, (x - e_px) * mult - friction, "stop"))
                state = STATE_FLAT
            else:
                if H[i] > hi_water:
                    hi_water = H[i]
                stop_px = max(stop_px, hi_water - K * ATR[i])
        if state == STATE_FLAT and SIG[i]:
            sig_atr = ATR[i]
            if not np.isnan(sig_atr):
                state = STATE_PENDING
    return pd.DataFrame(out, columns=["exit_time", "entry_px", "exit_px", "pnl", "reason"])
