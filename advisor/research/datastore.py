"""Nightly price-panel cache — the cross-section every factor computes from.

Downloads 2y of daily OHLCV for the full universe (~1,550 names + ETFs) in
batches via yfinance and stores wide parquet panels (close/high/low/volume,
date x ticker) under advisor/data/research/panels/. Full rebuild each run —
simple beats clever; takes ~5-10 min on the nightly schedule.

CLI: python -m advisor.research.datastore [--subset N]   (subset = smoke test)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESEARCH_DIR = Path(os.environ.get("ADVISOR_DATA_DIR",
                                   str(REPO_ROOT / "advisor" / "data"))) / "research"
PANEL_DIR = RESEARCH_DIR / "panels"
BUILDS_DIR = PANEL_DIR / "builds"
CURRENT = PANEL_DIR / "CURRENT"
BATCH = 200
FIELDS = ["Close", "High", "Low", "Volume"]


def code_version() -> dict:
    """Which code produced this artifact.

    The panel serving every factor computation on 2026-09-03 was built at
    06:01 by code replaced at 11:56 — its meta was missing fields the current
    datastore writes, while still declaring schema_version 2, so a version
    check could not catch it. Nothing invalidated a stale artifact after a
    code change and nothing recorded which code made it.

    Reads the PRODUCTION checkout (git rev-parse in the repo root), plus the
    deploy stamp naming the source commit rsynced in. Both are best-effort:
    an artifact must still build on a box without git.
    """
    import subprocess
    out = {"git_sha": None, "git_dirty": None, "deployed_from": None}
    try:
        out["git_sha"] = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10).stdout.strip() or None
        out["git_dirty"] = bool(subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
            capture_output=True, text=True, timeout=10).stdout.strip())
    except Exception:
        pass
    try:
        out["deployed_from"] = (REPO_ROOT / ".deployed_from").read_text().strip()
    except OSError:
        pass
    return out


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

    def fetch(symbols: list[str], batch_size: int, label: str) -> None:
        nonlocal failed
        for i in range(0, len(symbols), batch_size):
            chunk = symbols[i:i + batch_size]
            try:
                data = yf.download(chunk, period="2y", interval="1d", auto_adjust=True,
                                   progress=False, group_by="column", threads=True,
                                   timeout=30)
                for f in FIELDS:
                    if f in data.columns.get_level_values(0):
                        frame = data[f]
                        if isinstance(frame, pd.Series):
                            frame = frame.to_frame(name=chunk[0])
                        frames[f].append(frame)
            except Exception as exc:
                failed += len(chunk)
                print(f"[datastore] {label} batch {i//batch_size} failed: {exc}",
                      file=sys.stderr)
            print(f"[datastore] {label} {min(i+batch_size, len(symbols))}/"
                  f"{len(symbols)} ({time.time()-t0:.0f}s)", flush=True)

    fetch(tickers, BATCH, "initial")
    # Yahoo occasionally returns a syntactically successful but mostly empty
    # large batch. Retry absent symbols in small batches; exceptions alone do
    # not describe this failure mode (the 2026-07-16 cache had 10% survival
    # while reporting zero failed batches).
    for attempt in range(1, 3):
        observed = set()
        if frames["Close"]:
            preliminary = pd.concat(frames["Close"], axis=1)
            observed = set(preliminary.columns[preliminary.notna().sum() >= 60])
        missing = [t for t in dict.fromkeys(tickers) if t not in observed]
        if not missing:
            break
        print(f"[datastore] recovery pass {attempt}: {len(missing)} missing symbols",
              flush=True)
        fetch(missing, 25, f"recovery-{attempt}")
    panels = {}
    for f in FIELDS:
        panel = pd.concat(frames[f], axis=1)
        panel = panel.loc[:, ~panel.columns.duplicated()]
        good = panel.columns[panel.notna().sum() >= 60]
        panels[f] = panel[good]
    # Yahoo encodes a missing price as 0.0 on partial bars — seen 2026-09-02,
    # ^VIX came back High=Low=0.0 with a valid Close, which tripped the
    # quality gate's nonpositive/OHLC-relationship checks and failed the whole
    # build on a benchmark. A zero price is not an observation; treat it as
    # missing so the gate judges real data. Volume is deliberately excluded —
    # zero volume is legitimate.
    for f in ("Close", "High", "Low"):
        panels[f] = panels[f].where(panels[f] > 0)
    # Drop holiday-artifact rows (e.g. Memorial Day appearing with only ^VIX
    # populated) — one all-NaN row poisons every rolling window downstream.
    coverage = panels["Close"].notna().mean(axis=1)
    good_rows = coverage[coverage > 0.5].index
    # All fields must have exactly the same dates and tickers.  Inner alignment
    # avoids a partially populated field changing the effective universe.
    common_cols = panels["Close"].columns
    common_rows = panels["Close"].index
    for f in FIELDS[1:]:
        common_cols = common_cols.intersection(panels[f].columns, sort=False)
        common_rows = common_rows.intersection(panels[f].index, sort=False)
    stats = {}
    for f in FIELDS:
        panels[f] = panels[f].loc[common_rows.intersection(good_rows), common_cols].sort_index()
        stats[f] = panels[f].shape

    from advisor.research.data_quality import assess
    quality = assess(panels, requested=len(set(tickers)))
    if not quality["ok"]:
        raise RuntimeError("price-panel quality gate failed: " + " | ".join(quality["errors"]))

    built_unix = time.time()
    build_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(built_unix))
    PANEL_DIR.mkdir(parents=True, exist_ok=True)
    BUILDS_DIR.mkdir(parents=True, exist_ok=True)
    temp_dir = BUILDS_DIR / f".{build_id}.{os.getpid()}.tmp"
    final_dir = BUILDS_DIR / build_id
    temp_dir.mkdir()
    hashes = {}
    try:
        for f in FIELDS:
            path = temp_dir / f"{f.lower()}.parquet"
            panels[f].to_parquet(path)
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        meta = {"schema_version": 3, "build_id": build_id,
                "code_version": code_version(),
                "built_unix": built_unix, "n_tickers_requested": len(set(tickers)),
                "provider": "Yahoo Finance via yfinance",
                "price_adjustment": "auto_adjust=True (split/dividend adjusted OHLC)",
                "intended_use": "research fallback; not licensed for commercial redistribution",
                "failed_batches_tickers": failed,
                "n_tickers_survived": len(common_cols),
                "shapes": {k: list(v) for k, v in stats.items()},
                "sha256": hashes, "quality": quality}
        (temp_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        if final_dir.exists():
            raise RuntimeError(f"panel build id already exists: {build_id}")
        os.replace(temp_dir, final_dir)
        pointer_tmp = PANEL_DIR / f".CURRENT.{os.getpid()}.tmp"
        pointer_tmp.write_text(build_id + "\n")
        os.replace(pointer_tmp, CURRENT)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    print(f"[datastore] done in {time.time()-t0:.0f}s  close panel: {stats['Close']}")
    return meta


def current_build_dir() -> Path:
    """Resolve the immutable current build; support pre-v2 caches for rollout."""
    try:
        build_id = CURRENT.read_text().strip()
        if not build_id or Path(build_id).name != build_id:
            raise ValueError("invalid CURRENT pointer")
        path = BUILDS_DIR / build_id
        if not path.is_dir():
            raise FileNotFoundError(path)
        return path
    except FileNotFoundError:
        return PANEL_DIR


def load_panel(field: str):
    import pandas as pd
    return pd.read_parquet(current_build_dir() / f"{field.lower()}.parquet")


def current_meta() -> dict:
    return json.loads((current_build_dir() / "meta.json").read_text())


def panel_age_hours() -> float:
    try:
        meta = current_meta()
        return (time.time() - meta["built_unix"]) / 3600
    except Exception:
        return float("inf")


if __name__ == "__main__":
    n = None
    if "--subset" in sys.argv:
        n = int(sys.argv[sys.argv.index("--subset") + 1])
    build(subset=n)
