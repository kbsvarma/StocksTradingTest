"""Nightly price-panel cache — the cross-section every factor computes from.

Downloads 2y of daily OHLCV for the full universe (~1,550 names + ETFs) in
batches via yfinance and stores wide parquet panels (close/high/low/volume,
date x ticker) under advisor/data/research/panels/. Full rebuild each run —
simple beats clever; takes ~5-10 min on the nightly schedule.

CLI: python -m advisor.research.datastore [--subset N]   (subset = smoke test)
"""
from __future__ import annotations

import os
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESEARCH_DIR = Path(os.environ.get("ADVISOR_DATA_DIR",
                                   str(REPO_ROOT / "advisor" / "data"))) / "research"
PANEL_DIR = RESEARCH_DIR / "panels"
BATCH = 200
FIELDS = ["Close", "High", "Low", "Volume"]


def build(subset: int | None = None) -> dict:
    import pandas as pd
    import yfinance as yf
    from advisor.research.universe import load

    u = load()
    tickers = sorted(u["stocks"]) + [t for t in u["etfs"] if t not in ("^VIX", "^VIX3M", "^TNX")]
    tickers += ["^VIX", "^VIX3M", "^TNX"]
    if subset:
        tickers = tickers[:subset] + ["SPY", "^VIX"]
    t0 = time.time()
    frames: dict[str, list] = {f: [] for f in FIELDS}
    failed = 0
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i + BATCH]
        try:
            data = yf.download(chunk, period="2y", interval="1d", auto_adjust=True,
                               progress=False, group_by="column", threads=True)
            for f in FIELDS:
                if f in data.columns.get_level_values(0):
                    frames[f].append(data[f])
        except Exception as exc:
            failed += len(chunk)
            print(f"[datastore] batch {i//BATCH} failed: {exc}", file=sys.stderr)
        print(f"[datastore] {min(i+BATCH, len(tickers))}/{len(tickers)} "
              f"({time.time()-t0:.0f}s)", flush=True)
    PANEL_DIR.mkdir(parents=True, exist_ok=True)
    panels = {}
    for f in FIELDS:
        panel = pd.concat(frames[f], axis=1)
        panel = panel.loc[:, ~panel.columns.duplicated()]
        good = panel.columns[panel.notna().sum() >= 60]
        panels[f] = panel[good]
    # Drop holiday-artifact rows (e.g. Memorial Day appearing with only ^VIX
    # populated) — one all-NaN row poisons every rolling window downstream.
    coverage = panels["Close"].notna().mean(axis=1)
    good_rows = coverage[coverage > 0.5].index
    stats = {}
    for f in FIELDS:
        panels[f] = panels[f].loc[panels[f].index.intersection(good_rows)]
        panels[f].to_parquet(PANEL_DIR / f"{f.lower()}.parquet")
        stats[f] = panels[f].shape
    meta = {"built_unix": time.time(), "n_tickers_requested": len(tickers),
            "failed_batches_tickers": failed, "shapes": {k: list(v) for k, v in stats.items()}}
    import json
    (PANEL_DIR / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"[datastore] done in {time.time()-t0:.0f}s  close panel: {stats['Close']}")
    return meta


def load_panel(field: str):
    import pandas as pd
    return pd.read_parquet(PANEL_DIR / f"{field.lower()}.parquet")


def panel_age_hours() -> float:
    try:
        import json
        meta = json.loads((PANEL_DIR / "meta.json").read_text())
        return (time.time() - meta["built_unix"]) / 3600
    except Exception:
        return float("inf")


if __name__ == "__main__":
    n = None
    if "--subset" in sys.argv:
        n = int(sys.argv[sys.argv.index("--subset") + 1])
    build(subset=n)
