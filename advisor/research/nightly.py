"""Nightly research job — builds everything the morning brief computes from.

universe refresh (weekly) → full price-panel rebuild → factor sheet.
Writes signals to advisor/data/research/signals_latest.{json,txt} (+ dated
copy) so run_brief.sh just copies the latest into the day's context dir.

launchd: com.stockstest.advisor-research (06:00 ET weekdays).
CLI:    python -m advisor.research.nightly [--subset N]
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR, build
from advisor.research.factors import compute, render
from advisor.research.universe import load as load_universe

ET = ZoneInfo("America/New_York")


def run(subset: int | None = None) -> int:
    t0 = time.time()
    print(f"[nightly] start {datetime.now(ET).isoformat()}", flush=True)
    u = load_universe()
    print(f"[nightly] universe: {u['n_stocks']} stocks", flush=True)
    build(subset=subset)
    s = compute(top=20)
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(ET).date().isoformat()
    # NEW-ENTRANT flags: ranks are sticky (autocorr .92/21d, validate.py) —
    # a name newly arriving on the sheet is a genuine event; mark it.
    try:
        prior_files = sorted(RESEARCH_DIR.glob("signals_2*.json"))
        prior = json.loads(prior_files[-1].read_text()) if prior_files else {}
        for side in ("longs", "shorts"):
            seen = {x["ticker"] for x in prior.get(side, [])}
            for x in s.get(side, []):
                x["new_entrant"] = x["ticker"] not in seen if seen else False
        n_new = sum(1 for x in s.get("longs", []) if x.get("new_entrant"))
        print(f"[nightly] new long-sheet entrants vs prior run: {n_new}")
    except Exception as exc:
        print(f"[nightly] new-entrant diff failed (non-fatal): {exc}")
    (RESEARCH_DIR / "signals_latest.json").write_text(json.dumps(s, indent=2))
    txt = render(s)
    (RESEARCH_DIR / "signals_latest.txt").write_text(txt)
    shutil.copy(RESEARCH_DIR / "signals_latest.json", RESEARCH_DIR / f"signals_{day}.json")
    print(txt)
    print(f"[nightly] done in {time.time()-t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    n = int(sys.argv[sys.argv.index("--subset") + 1]) if "--subset" in sys.argv else None
    sys.exit(run(subset=n))
