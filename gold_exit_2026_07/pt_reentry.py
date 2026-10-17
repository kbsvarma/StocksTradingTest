"""Step 2 — pre-registered PT+re-entry cells per PREREG_PT_REENTRY.md."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from mfe_diagnostic import SCFG, MULT, FR, seg_a, seg_b, simulate_mfe  # noqa: E402
import strategy  # noqa: E402

CELLS = [0.5, 1.0, 1.5, 2.0]
GATE_A, GATE_B, GATE_C = 10974.0, -1497.0, 11470.0


def simulate_pt(B, start, pt_r):
    f = strategy.signal_frame(B, SCFG)
    sig = f["signal"].values
    atr = f["atr"].values
    O, H, L, C = (B[c].values for c in ("open", "high", "low", "close"))
    R_, ts = B["roll"].values, B.index
    rows = []
    pos = pend = 0
    e = hw = st = sa = tgt = np.nan
    e_ts = None
    e_i = -1
    for i in range(len(B)):
        if pend and R_[i]:
            pend = 0
        if pend:
            pos, pend = 1, 0
            e, hw, sa = O[i], O[i], atr[i - 1]
            st = e - SCFG["k_trail"] * sa
            tgt = e + pt_r * 2.0 * sa
            e_ts, e_i = ts[i], i
            if np.isnan(st):
                pos = 0
        if pos and R_[i]:
            x = C[i - 1] if i else O[i]
            rows.append((e_ts, ts[i], x - e, "roll"))
            pos = 0
        if pos:
            if L[i] <= st:                     # stop-first pessimism
                x = min(st, O[i])
                rows.append((e_ts, ts[i], x - e, "stop"))
                pos = 0
            elif i > e_i and H[i] >= tgt:      # banked at the resting limit
                rows.append((e_ts, ts[i], tgt - e, "target"))
                pos = 0
            else:
                hw = max(hw, H[i])
                st = max(st, hw - SCFG["k_trail"] * atr[i])
        if pos == 0 and pend == 0 and sig[i] and i > SCFG["donchian_n"]:
            pend = 1                           # the natural "try again"
    T = pd.DataFrame(rows, columns=["entry_time", "exit_time", "points",
                                    "reason"])
    T = T[T["entry_time"] >= start].reset_index(drop=True)
    T["pnl"] = T["points"] * MULT - FR
    return T


def main():
    (BA, sa_), (BB, sb_) = seg_a(), seg_b()
    base_a = simulate_mfe(BA, sa_)["pnl"].sum()
    base_b = simulate_mfe(BB, sb_)["pnl"].sum()
    print(f"trail baseline: A ${base_a:,.0f}  B ${base_b:,.0f}  "
          f"combined ${base_a + base_b:,.0f}\n")
    for pt in CELLS:
        TA = simulate_pt(BA, sa_, pt)
        TB = simulate_pt(BB, sb_, pt)
        a, b = TA["pnl"].sum(), TB["pnl"].sum()
        comb = a + b
        n = len(TA) + len(TB)
        banked = (pd.concat([TA, TB])["reason"] == "target").mean() * 100
        ok = comb > GATE_A and a >= GATE_B and b >= GATE_C
        print(f"PT {pt:>3}R: n={n:>5} banked {banked:4.1f}%  "
              f"A ${a:>8,.0f}  B ${b:>8,.0f}  combined ${comb:>9,.0f}  "
              f"-> {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
