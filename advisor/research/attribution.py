"""Attribution by idea source + rejected-idea counterfactuals.

Answers "which candidate generators earn their place" with hard numbers.
Deterministic; run by the weekly review. Minimum-sample discipline is
printed with every table (INTELLIGENCE_PLAN §5): a generator's weighting is
untouchable until >=10 resolved calls from it, watch-listed 10–19,
promotion/demotion proposals need >=20 (and go to the user).

CLI: python -m advisor.research.attribution [--json] [--no-counterfactuals]
Writes advisor/data/research/attribution_latest.json.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from advisor.research.outcomes import (forward_return, rejected_ideas,
                                       resolved_views)

ET = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT = REPO_ROOT / "advisor" / "data" / "research" / "attribution_latest.json"


def _gate(n: int) -> str:
    if n < 10:
        return "untouchable (n<10)"
    if n < 20:
        return "watch (10<=n<20)"
    return "proposable (n>=20, user decides)"


def by_source() -> dict:
    table: dict[str, dict] = {}
    for r in resolved_views():
        src = r.get("source") or "unlabeled(pre-v2)"
        t = table.setdefault(src, {"n": 0, "decided": 0, "wins": 0,
                                   "rs": [], "r_basis": set()})
        t["n"] += 1
        if r.get("win") is not None:        # ambiguous closes don't count as losses
            t["decided"] += 1
            t["wins"] += r["win"]
        if r.get("realized_r") is not None:
            t["rs"].append(r["realized_r"])
            t["r_basis"].add(r.get("r_basis", "?"))
    out = {}
    for src, t in sorted(table.items(), key=lambda kv: -kv[1]["n"]):
        rs = t["rs"]
        out[src] = {
            "n": t["n"],
            "n_decided": t["decided"],
            "hit_rate": round(t["wins"] / t["decided"], 3) if t["decided"] else None,
            "n_with_r": len(rs),
            "avg_realized_r": round(sum(rs) / len(rs), 3) if rs else None,
            "r_basis": sorted(t["r_basis"]),
            "sample_gate": _gate(t["n"]),
        }
    return out


def rejected_counterfactuals(fetch: bool = True) -> list[dict]:
    out = []
    for r in rejected_ideas():
        row = {"id": r["id"], "instrument": r.get("instrument"),
               "yf_ticker": r.get("yf_ticker"),
               "direction": r.get("direction"),
               "killed_by": (r.get("killed_by") or r.get("note") or "")[:120],
               "ref_px": r.get("ref_px"), "ref_ts": r.get("ref_ts")}
        if fetch and isinstance(r.get("ref_px"), (int, float)) and r.get("yf_ticker"):
            fwd = forward_return(r["yf_ticker"], r["ref_px"], r.get("ref_ts"))
            if fwd:
                row.update(fwd)
                # a rejected LONG that went up = the kill cost us (and vice versa)
                if fwd.get("fwd_return_pct") is not None:
                    sign = 1 if (r.get("direction") or "long").lower() != "short" else -1
                    row["kill_cost_pct"] = round(sign * fwd["fwd_return_pct"], 2)
        out.append(row)
    return out


def compute(fetch: bool = True) -> dict:
    return {"as_of": datetime.now(ET).isoformat(),
            "by_source": by_source(),
            "rejected_counterfactuals": rejected_counterfactuals(fetch),
            "method": "resolved views grouped by v2 `source`; R from explicit "
                      "fields else level-approximation (labeled); "
                      "counterfactuals = fwd return since code-stamped ref_px "
                      "vs SPY, yfinance delayed"}


def main() -> int:
    fetch = "--no-counterfactuals" not in sys.argv
    res = compute(fetch)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=2))
    if "--json" in sys.argv:
        print(json.dumps(res, indent=2))
        return 0
    print("attribution by source:")
    for src, t in res["by_source"].items():
        print(f"  {src:<22} n={t['n']:<3} hit={t['hit_rate']} "
              f"avgR={t['avg_realized_r']} [{t['sample_gate']}]")
    if not res["by_source"]:
        print("  (no resolved views yet)")
    cfs = res["rejected_counterfactuals"]
    print(f"rejected counterfactuals: {len(cfs)}")
    for c in cfs:
        if c.get("fwd_return_pct") is not None:
            print(f"  {c['id']} {c.get('yf_ticker'):<6} "
                  f"fwd={c['fwd_return_pct']:+.1f}% vs SPY "
                  f"{c.get('spy_return_pct')}% kill_cost={c.get('kill_cost_pct')}%")
    print(f"→ {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
