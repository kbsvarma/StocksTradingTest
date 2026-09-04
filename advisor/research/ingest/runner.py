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
    # Candidate-specific, time-sensitive endpoints run before broad profile
    # fetches so a Yahoo throttle cannot starve the highest-value inputs.
    "estimates": (estimates.build, RESEARCH_DIR / "estimates"),
    "events": (events.build, RESEARCH_DIR / "events"),
    "info": (snapshots.build, RESEARCH_DIR / "snapshots" / "info"),
}
# IV runs ONLY when explicitly requested (--datasets iv): overnight Yahoo
# serves placeholder IVs (~1.56%) that fail the sanity floor — the snapshot
# must run near the close (com.stockstest.advisor-ivsnap, 16:15 ET).
EXPLICIT_DATASETS = {
    "iv": (iv_surface.build, RESEARCH_DIR / "options" / "iv"),
}
RETAIN_DAYS = 730   # prune dt= partitions older than ~2y (matches OHLCV panels)
INFO_DAILY_BUDGET = 400
INFO_COVERAGE_DAYS = 7


def _apply_quality(name: str, res: dict, requested: int) -> dict:
    errors = int(res.get("errors") or 0)
    fetch_ratio = ((requested - errors) / requested) if requested else 0.0
    if name == "info" and int(res.get("coverage_universe") or 0):
        success_ratio = (int(res.get("coverage_rows") or 0)
                         / int(res["coverage_universe"]))
        res["fetch_success_ratio"] = round(fetch_ratio, 4)
    else:
        success_ratio = fetch_ratio
    res["requested"] = requested
    res["success_ratio"] = round(success_ratio, 4)
    res["ok"] = name == "iv" or success_ratio >= 0.80
    if not res["ok"]:
        res["quality_error"] = f"coverage {success_ratio:.1%} below 80% minimum"
    return res


def _rotating_info_set(tickers: list[str], active: list[str],
                       *, day_ordinal: int | None = None,
                       budget: int = INFO_DAILY_BUDGET) -> list[str]:
    """Prioritize active names, then rotate deterministically through universe."""
    if len(tickers) <= budget:
        return list(tickers)
    valid = set(tickers)
    priority = [ticker for ticker in active if ticker in valid]
    priority = list(dict.fromkeys(priority))[:budget]
    remaining_n = budget - len(priority)
    if remaining_n <= 0:
        return sorted(priority)
    ordinal = day_ordinal if day_ordinal is not None else datetime.now(ET).date().toordinal()
    start = (ordinal * remaining_n) % len(tickers)
    rotated = tickers[start:] + tickers[:start]
    priority_set = set(priority)
    selected = priority + [ticker for ticker in rotated
                           if ticker not in priority_set][:remaining_n]
    return sorted(selected)


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
    # Yahoo request budget (2026-07-02: full-universe estimates got the
    # account rate-limited into a 401-crumb penalty box). estimates/events
    # are ~5 requests/ticker → ACTIVE SET on weekdays, full universe only on
    # Saturday's quiet sweep. info is 1 request/ticker → full daily is fine.
    is_saturday = datetime.now(ET).weekday() == 5
    active = [t for t in _active_set() if t in set(tickers)]
    scoped = tickers if (is_saturday or subset) else active or tickers[:150]
    info_scope = tickers if (is_saturday or subset) else _rotating_info_set(tickers, active)
    scope_by_dataset = {"info": info_scope, "estimates": scoped, "events": scoped}
    manifest = {"as_of": datetime.now(ET).isoformat(), "n_tickers": len(tickers),
                "scope": {"estimates_events": ("full(saturday)" if is_saturday
                                               else f"active_set({len(scoped)})"),
                          "info": ("full(saturday)" if is_saturday or subset
                                   else f"active_plus_rotating_shard({len(info_scope)})")},
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
        scope = scope_by_dataset.get(name, tickers)
        print(f"[ingest] {name}: {len(scope)} tickers …", flush=True)
        try:
            res = fn(scope, out_dir)
            if name == "info":
                res.update(snapshots.build_coverage(
                    tickers, out_dir, max_age_days=INFO_COVERAGE_DAYS))
            # A syntactically completed fetch is not a valid cross-section.
            # For IV, thin chains are deliberately dropped and do not indicate
            # transport failure; all other snapshots require >=80% success.
            _apply_quality(name, res, len(scope))
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
            from advisor.research import edgar, form4
            res = edgar.form4_sweep()
            if res.get("ok"):
                # Open the documents the sweep indexes. Without this the
                # cluster detector had no transactions and fell back to
                # yfinance text-matching for direction; with it, 833 parsed
                # transactions showed open-market purchases are 2.2% of Form 4
                # activity — the other 97.8% is compensation mechanics that
                # the old filing-count heuristic was treating as signal.
                res["parsed"] = form4.enrich(days=7, verbose=False)
                cl = form4.detect_clusters()
                res["n_clusters"] = len(cl.get("clusters", []))
                res["n_open_market_buys"] = cl.get("n_open_market_buys")
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
