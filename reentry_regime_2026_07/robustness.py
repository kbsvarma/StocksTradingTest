import sys
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent))
exec(open("run_cells.py").read().split("rows = []")[0])   # reuse setup

for pctl in (0.25, 0.30, 0.35):
    for mode in ("M1", "M3"):
        r = run(h2=dict(det="WIDTH", mode=mode, pctl=pctl, h1sub=None))
        print(f"WIDTH_{mode} pctl={pctl}: total {r['total']:>7} "
              f"ratio {r['ratio']:>6} worst {r['worst_yr']:>6} "
              f"trades {r['trades']:>5} halves {r['h1_']}/{r['h2_']}",
              flush=True)
base = run()
print("BASELINE            total", base["total"], "ratio", base["ratio"],
      "worst", base["worst_yr"], "halves", f"{base['h1_']}/{base['h2_']}")
