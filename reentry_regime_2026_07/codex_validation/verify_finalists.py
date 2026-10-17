"""Independent, deliberately duplicated verification of the two finalists.

This does not call ``engine.simulate`` for the candidate paths. The small FSM
is copied directly from the authoritative production research simulator and
contains only one candidate hook at a time.
"""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine  # noqa: E402
import run_study as rs  # noqa: E402


def direct(bars, sym, week_qty, *, width_chop=None, atr0_horizon=None):
    f = engine.strategy.signal_frame(bars, engine.rg.SCFG)
    atr = f["atr"].to_numpy()
    sig = f["signal"].to_numpy()
    gates = (f["vol_ok"].to_numpy() & f["session_ok"].to_numpy()
             & f["atr"].notna().to_numpy())
    o, h, low, c = (bars[x].to_numpy()
                    for x in ("open", "high", "low", "close"))
    roll, stamps = bars["roll"].to_numpy(), bars.index
    out = []
    pos = pend = False
    entry = high_water = stop = np.nan
    entry_time = None
    qty = 0
    pending_qty = 0
    pending_kind = ""
    entry_kind = ""
    stopped_bar = None
    stopped_fill = np.nan

    for i in range(len(bars)):
        if pend and roll[i]:
            pend = False
        if pend:
            pos, pend = True, False
            qty = pending_qty
            entry_kind = pending_kind
            entry = high_water = o[i]
            stop = entry - engine.rg.SCFG["k_trail"] * atr[i - 1]
            entry_time = stamps[i]
            stopped_bar = None
            if not np.isfinite(stop):
                pos = False
        if pos and roll[i]:
            x = c[i - 1] if i else o[i]
            out.append((entry_time, stamps[i], sym, entry, x, x - entry,
                        "roll", 1, qty, entry_kind, False))
            pos = False
            stopped_bar = None
        if pos:
            if low[i] <= stop:
                x = min(stop, o[i])
                out.append((entry_time, stamps[i], sym, entry, x, x - entry,
                            "stop", 1, qty, entry_kind, False))
                pos = False
                stopped_bar, stopped_fill = i, x
            else:
                high_water = max(high_water, h[i])
                stop = max(stop, high_water - engine.rg.SCFG["k_trail"] * atr[i])
        if not pos and roll[i]:
            stopped_bar = None
        if not pos and not pend and i > engine.rg.SCFG["donchian_n"]:
            fire = bool(sig[i])
            kind = "D20" if fire else ""
            if (not fire and atr0_horizon is not None
                    and stopped_bar is not None
                    and 1 <= i - stopped_bar <= atr0_horizon
                    and gates[i] and c[i] > stopped_fill):
                fire, kind = True, "H1_ATR"
            if fire and width_chop is not None and width_chop[i]:
                fire = False
            if fire:
                q = int(week_qty(stamps[i]))
                if q > 0:
                    pend, pending_qty, pending_kind = True, q, kind

    cols = ["entry_time", "exit_time", "sym", "entry_px", "exit_px",
            "points", "reason", "dir", "qty", "entry_kind", "entry_chop"]
    result = pd.DataFrame(out, columns=cols)
    return result[result["entry_time"] >= engine.rg.START].reset_index(drop=True)


def main():
    bars, policy, feats = rs.load_inputs()
    cases = {
        "WIDTH20_BLOCK": (None, {"detector": "WIDTH20", "mode": "BLOCK"}),
        "ATR0_H12": ({"kind": "ATR", "m": 0.0, "horizon": 12}, None),
    }
    for label, (h1, h2) in cases.items():
        direct_pieces, engine_pieces = [], []
        for sym, b in bars.items():
            wq = rs.weight_function(policy, sym)
            got = engine.simulate(b, sym, wq, feats[sym], h1=h1, h2=h2)
            ref = direct(
                b, sym, wq,
                width_chop=(feats[sym]["chop"]["WIDTH20"]
                            if label == "WIDTH20_BLOCK" else None),
                atr0_horizon=(12 if label == "ATR0_H12" else None))
            if not got.equals(ref):
                raise AssertionError(f"{label}/{sym}: independent mismatch")
            print(f"{label}/{sym}: {len(got):,} trades IDENTICAL")
            direct_pieces.append(ref)
            engine_pieces.append(got)
        result = pd.concat(direct_pieces).sort_values("exit_time")
        print(label, rs.metrics(result, bars))


if __name__ == "__main__":
    main()
