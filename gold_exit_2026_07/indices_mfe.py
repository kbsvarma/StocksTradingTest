"""Same exit diagnostic for the index core: NQ (Nasdaq), ES (S&P), YM (Dow).

Per-instrument 1-contract analysis (micro multipliers), live locked config,
trades 2011+ — the same MFE distribution + banking-counterfactual ladder
run for gold in mfe_diagnostic.py. DIAGNOSTIC ONLY.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "trend_bot"))
sys.path.insert(0, str(ROOT / "trend_bot" / "research"))
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd  # noqa: E402
import rotation_grid as rg  # noqa: E402
from mfe_diagnostic import simulate_mfe, describe  # noqa: E402

START = pd.Timestamp("2011-01-01", tz="UTC")
NAMES = {"NQ": "Nasdaq (MNQ $2/pt)", "ES": "S&P (MES $5/pt)",
         "YM": "Dow (MYM $0.5/pt)"}


def main():
    for sym, mult in rg.MULT.items():
        B = rg.load_60m(sym)
        T = simulate_mfe(B, START, mult=mult)
        describe(NAMES[sym], T, mult=mult)
        T.to_parquet(Path(__file__).parent / f"mfe_trades_{sym}.parquet")


if __name__ == "__main__":
    main()
