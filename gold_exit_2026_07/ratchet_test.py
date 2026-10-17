"""Profit-locked ratchet trail — pre-registered per PREREG_RATCHET.md."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path[:0] = [str(HERE), str(ROOT / "trend_bot"), str(ROOT / "trend_bot" / "research")]

from mfe_diagnostic import SCFG, FR, seg_a, seg_b  # noqa: E402
import rotation_grid as rg                          # noqa: E402
import strategy                                     # noqa: E402

CELLS = [(1.5, 1.0), (1.5, 1.5), (3.0, 1.0), (3.0, 1.5)]


def simulate_ratchet(B, start, mult, trigger_r=None, k_tight=None):
    """Locked long FSM + optional profit-triggered trail tightening."""
    f = strategy.signal_frame(B, SCFG)
    sig = f["signal"].values
    atr = f["atr"].values
    O, H, L, C = (B[c].values for c in ("open", "high", "low", "close"))
    R_, ts = B["roll"].values, B.index
    rows = []
    pos = pend = 0
    e = hw = st = sa = np.nan
    k_eff = SCFG["k_trail"]
    e_ts = None
    for i in range(len(B)):
        if pend and R_[i]:
            pend = 0
        if pend:
            pos, pend = 1, 0
            e, hw, sa = O[i], O[i], atr[i - 1]
            st = e - SCFG["k_trail"] * sa
            k_eff = SCFG["k_trail"]
            e_ts = ts[i]
            if np.isnan(st):
                pos = 0
        if pos and R_[i]:
            x = C[i - 1] if i else O[i]
            rows.append((e_ts, ts[i], x - e))
            pos = 0
        if pos:
            if L[i] <= st:
                x = min(st, O[i])
                rows.append((e_ts, ts[i], x - e))
                pos = 0
            else:
                hw = max(hw, H[i])
                if (trigger_r is not None
                        and hw - e >= trigger_r * 2.0 * sa):
                    k_eff = k_tight            # profit lock engaged
                st = max(st, hw - k_eff * atr[i])
        if pos == 0 and pend == 0 and sig[i] and i > SCFG["donchian_n"]:
            pend = 1
    T = pd.DataFrame(rows, columns=["entry_time", "exit_time", "points"])
    T = T[T["entry_time"] >= start].reset_index(drop=True)
    T["pnl"] = T["points"] * mult - FR
    return T


def run_instrument(name, frames, mult):
    """frames: list of (bars, start). Returns dict cell->(total, worst_yr)."""
    out = {}
    for label, args in [("base", (None, None))] + [
            (f"T{t}/K{k}", (t, k)) for t, k in CELLS]:
        pnl = pd.concat([
            simulate_ratchet(B, s, mult, *args).set_index("exit_time")["pnl"]
            for B, s in frames])
        y = pnl.groupby(pnl.index.year).sum()
        out[label] = (float(pnl.sum()), float(y.min()))
    return out


def main():
    rg.START = pd.Timestamp("2011-01-01", tz="UTC")
    frames = {
        "GOLD": ([seg_a(), seg_b()], 10.0),
        "NQ": ([(rg.load_60m("NQ"), rg.START)], 2.0),
        "ES": ([(rg.load_60m("ES"), rg.START)], 5.0),
        "YM": ([(rg.load_60m("YM"), rg.START)], 0.5),
    }
    results = {}
    for name, (fr, mult) in frames.items():
        fr = [(B, (s if isinstance(s, pd.Timestamp) else rg.START))
              for B, s in fr]
        results[name] = run_instrument(name, fr, mult)
        b = results[name]["base"]
        print(f"{name}: baseline ${b[0]:,.0f} (worst-yr ${b[1]:,.0f})")

    print(f"\n{'cell':<10}", *(f"{n:>10}" for n in results), "  beats>=3/4  worst-yr-ok  VERDICT")
    for t, k in CELLS:
        lab = f"T{t}/K{k}"
        beats = 0
        wy_ok = True
        row = []
        for name in results:
            tot, wy = results[name][lab]
            b_tot, b_wy = results[name]["base"]
            beats += tot > b_tot
            if wy < b_wy - 250:
                wy_ok = False
            row.append(f"{tot - b_tot:>+10,.0f}")
        verdict = "PASS" if (beats >= 3 and wy_ok) else "FAIL"
        print(f"{lab:<10}", *row, f"  {beats}/4", f"{str(wy_ok):>11}", f"  {verdict}")


if __name__ == "__main__":
    main()
