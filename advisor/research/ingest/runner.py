"""Ingest orchestrator — runs each snapshotter, checkpointed per dataset.

One dataset failing (yfinance breaks something quarterly) must never kill the
others or the factor build. Writes _meta/ingest_manifest.json so the morning
session can see exactly what's fresh and what failed — no silent staleness.

CLI: python -m advisor.research.ingest.runner [--subset N] [--datasets info,estimates,events]
Called by research/nightly.py after signals are written (so a slow or dead
ingest never delays the factor sheet).
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR
from advisor.research.ingest import estimates, events, iv_surface, snapshots
from advisor.research.universe import load as load_universe

ET = ZoneInfo("America/New_York")
MANIFEST = RESEARCH_DIR / "_meta" / "ingest_manifest.json"

DATASETS = {
    "info": (snapshots.build, RESEARCH_DIR / "snapshots" / "info"),
    "estimates": (estimates.build, RESEARCH_DIR / "estimates"),
    "events": (events.build, RESEARCH_DIR / "events"),
}
# IV runs ONLY when explicitly requested (--datasets iv): overnight Yahoo
# serves placeholder IVs (~1.56%) that fail the sanity floor — the snapshot
# must run near the close (com.stockstest.advisor-ivsnap, 16:15 ET).
EXPLICIT_DATASETS = {
    "iv": (iv_surface.build, RESEARCH_DIR / "options" / "iv"),
}
RETAIN_DAYS = 730   # prune dt= partitions older than ~2y (matches OHLCV panels)


def _prune(out_dir) -> int:
    cutoff = datetime.now(ET).date().toordinal() - RETAIN_DAYS
    n = 0
    for p in out_dir.glob("dt=*.parquet"):
        try:
            day = datetime.fromisoformat(p.stem.split("=", 1)[1]).date()
            if day.toordinal() < cutoff:
                p.unlink()
                n += 1
        except (ValueError, IndexError):
            continue
    return n


def _active_set() -> list[str]:
    """Names worth point-in-time fundamentals: factor-sheet longs/shorts/shock
    + open journal calls + existing dossiers. Full-universe EDGAR sweeps are
    deliberately out of scope (TUNING_NOTES)."""
    out: set[str] = set()
    try:
        s = json.loads((RESEARCH_DIR / "signals_latest.json").read_text())
        for side in ("longs", "shorts", "shock_candidates"):
            out.update(x["ticker"] for x in s.get(side, []))
    except Exception:
        pass
    try:
        from advisor.journal import effective
        for e in effective().values():
            if e.get("status") == "open" and e.get("yf_ticker"):
                t = e["yf_ticker"]
                if not any(x in t for x in ("^", "=F", "=X", "-USD")):
                    out.add(t)
    except Exception:
        pass
    try:
        dossiers = RESEARCH_DIR.parent / "knowledge" / "dossiers"
        out.update(p.name for p in dossiers.iterdir() if p.is_dir())
    except Exception:
        pass
    return sorted(out)


def run_all(subset: int | None = None, only: list[str] | None = None) -> dict:
    u = load_universe()
    tickers = sorted(u["stocks"].keys())   # stocks only — .info fundamentals are meaningless for ETFs
    if subset:
        tickers = tickers[:subset]
    manifest = {"as_of": datetime.now(ET).isoformat(), "n_tickers": len(tickers),
                "datasets": {}}
    # carry forward prior results for datasets not run tonight
    try:
        prior = json.loads(MANIFEST.read_text())
        manifest["datasets"].update(prior.get("datasets", {}))
    except Exception:
        pass
    all_datasets = {**DATASETS,
                    **{k: v for k, v in EXPLICIT_DATASETS.items()
                       if only and k in only}}
    for name, (fn, out_dir) in all_datasets.items():
        if only and name not in only:
            continue
        print(f"[ingest] {name}: {len(tickers)} tickers …", flush=True)
        try:
            res = fn(tickers, out_dir)
            res["ok"] = True
            res["pruned"] = _prune(out_dir)
        except Exception as exc:
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                   "trace": traceback.format_exc()[-800:]}
        res["as_of"] = datetime.now(ET).isoformat()
        manifest["datasets"][name] = res
        print(f"[ingest] {name}: {json.dumps({k: v for k, v in res.items() if k != 'trace'})}",
              flush=True)

    # EDGAR datasets (different call signatures — checkpointed individually)
    if not only or "form4" in (only or []):
        try:
            from advisor.research import edgar
            res = edgar.form4_sweep()
            if res.get("ok"):
                cl = edgar.detect_clusters()
                res["n_clusters"] = len(cl.get("clusters", []))
            res.pop("tickers", None)
        except Exception as exc:
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        res["as_of"] = datetime.now(ET).isoformat()
        manifest["datasets"]["form4"] = res
        print(f"[ingest] form4: {json.dumps(res)}", flush=True)
    if not only or "edgar_facts" in (only or []):
        try:
            from advisor.research import edgar
            res = edgar.facts_sweep(_active_set())
        except Exception as exc:
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        else:
            res["ok"] = True
        res["as_of"] = datetime.now(ET).isoformat()
        manifest["datasets"]["edgar_facts"] = res
        print(f"[ingest] edgar_facts: {json.dumps(res)}", flush=True)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2))
    tmp.replace(MANIFEST)
    return manifest


def main() -> int:
    subset = None
    if "--subset" in sys.argv:
        subset = int(sys.argv[sys.argv.index("--subset") + 1])
    only = None
    if "--datasets" in sys.argv:
        only = sys.argv[sys.argv.index("--datasets") + 1].split(",")
    m = run_all(subset=subset, only=only)
    bad = [k for k, v in m["datasets"].items() if not v.get("ok")]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
