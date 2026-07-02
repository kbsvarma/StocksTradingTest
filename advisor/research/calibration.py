"""Conviction calibration — Brier score of stated p_win vs realized outcomes.

Deterministic. The weekly review RUNS this and interprets; it never computes
its own numbers. Discipline (INTELLIGENCE_PLAN §5): no calibration
conclusions until n>=15 resolved; no conviction-map changes until n>=30 —
the script prints the gate so the session can't "forget" it.

CLI: python -m advisor.research.calibration [--json]
Writes advisor/data/research/calibration_latest.json as a side effect.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from advisor.research.outcomes import resolved_views

ET = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT = REPO_ROOT / "advisor" / "data" / "research" / "calibration_latest.json"

MIN_N_REPORT = 15
MIN_N_ACT = 30


def compute() -> dict:
    rows = resolved_views()
    scored = [r for r in rows
              if r.get("win") is not None and r.get("p_win_effective") is not None]
    excluded = len(rows) - len(scored)
    n = len(scored)

    brier = reliability = None
    if n:
        brier = round(sum((r["p_win_effective"] - r["win"]) ** 2 for r in scored) / n, 4)
        buckets: dict[str, dict] = {}
        for r in scored:
            key = f"{round(r['p_win_effective'], 2):.2f}"
            b = buckets.setdefault(key, {"n": 0, "wins": 0})
            b["n"] += 1
            b["wins"] += r["win"]
        reliability = {k: {**v, "realized_hit_rate": round(v["wins"] / v["n"], 3)}
                       for k, v in sorted(buckets.items())}

    return {
        "as_of": datetime.now(ET).isoformat(),
        "n_resolved_scored": n,
        "n_excluded_ambiguous": excluded,
        "brier": brier,
        "brier_baseline_always_50": 0.25,
        "reliability_by_stated_p": reliability,
        "sample_gate": ("LOW-N: no conclusions" if n < MIN_N_REPORT else
                        "report-only: no conviction-map changes" if n < MIN_N_ACT else
                        "actionable"),
        "gates": {"min_n_report": MIN_N_REPORT, "min_n_act": MIN_N_ACT},
        "method": "Brier over resolved views; p_win explicit else conviction map "
                  "high=0.70 medium=0.55; win=hit_target(1)/stopped(0), "
                  "time_stop/closed by realized sign when present else excluded",
    }


def main() -> int:
    res = compute()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=2))
    if "--json" in sys.argv:
        print(json.dumps(res, indent=2))
    else:
        b = res["brier"]
        print(f"calibration: n={res['n_resolved_scored']} "
              f"(+{res['n_excluded_ambiguous']} ambiguous excluded) "
              f"Brier={'—' if b is None else b} vs baseline 0.25 "
              f"[{res['sample_gate']}]")
        for p, r in (res["reliability_by_stated_p"] or {}).items():
            print(f"  stated p={p}: n={r['n']} realized={r['realized_hit_rate']}")
        print(f"→ {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
