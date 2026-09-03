"""Stratified candidate slate — what the morning session actually triages.

Replaces "read the top-20 momentum list" with a ~50-name slate drawn from
EVERY generator, each entry tagged with WHY it's here (source buckets feed
attribution — generators must earn their keep). Multi-bucket confluence
ranks first. The IPS 1-3-view ceiling is untouched: this widens the
funnel's mouth, not its exit.

Buckets: tactical_long/short (price composite) · pead_fresh · insider_cluster
· revision_leader · cheap_quality · new_entrant · squeeze_flag

CLI: python -m advisor.research.candidates
Writes advisor/data/research/candidates_latest.json (+ dated copy).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR

ET = ZoneInfo("America/New_York")
OUT = RESEARCH_DIR / "candidates_latest.json"

CAPS = {"tactical_long": 10, "tactical_short": 5, "pead_fresh": 6,
        "insider_cluster": 5, "revision_leader": 5, "cheap_quality": 5,
        "new_entrant": 5, "squeeze_flag": 3}

# Exploratory diagnostic only. Repeated rejects are retained for retrospective
# attribution, but are NOT injected into the candidate slate: the original
# 17/19 anecdote was selected from a tiny, non-registered sample and cannot
# establish a tradeable short edge.
REPEAT_KILL_WINDOW_D = 14
_STRETCH_PAT = None


def repeat_kills() -> list[dict]:
    import re
    from collections import defaultdict
    from datetime import timedelta
    global _STRETCH_PAT
    if _STRETCH_PAT is None:
        _STRETCH_PAT = re.compile(
            r"extended|overvalu|rich|stretch|reversion|unwind|no valuation|"
            r"R:R|no-moat|chas|crowded|parabol", re.I)
    from advisor.research.outcomes import rejected_ideas
    cutoff = (datetime.now(ET) - timedelta(days=REPEAT_KILL_WINDOW_D)).isoformat()
    by: dict[str, list] = defaultdict(list)
    for r in rejected_ideas():
        if (r.get("ts") or "") < cutoff or not r.get("yf_ticker"):
            continue
        by[r["yf_ticker"]].append(r)
    out = []
    for t, kills in by.items():
        if len(kills) < 2:
            continue
        if not any(_STRETCH_PAT.search(k.get("killed_by") or "") for k in kills):
            continue
        out.append({"ticker": t, "n_kills": len(kills),
                    "last_kill": max(k.get("ts", "") for k in kills)[:10],
                    "kill_reasons": [(k.get("killed_by") or "")[:70]
                                     for k in kills[-2:]]})
    return sorted(out, key=lambda x: -x["n_kills"])


def build() -> dict:
    import pandas as pd
    from advisor.research.factors_fundamental import compute_scores

    entries: dict[str, dict] = {}

    def add(ticker: str, bucket: str, **info) -> None:
        e = entries.setdefault(ticker, {"ticker": ticker, "buckets": [],
                                        "detail": {}})
        if bucket not in e["buckets"]:
            e["buckets"].append(bucket)
        e["detail"].update({k: v for k, v in info.items() if v is not None})

    # price-factor sheets
    try:
        s = json.loads((RESEARCH_DIR / "signals_latest.json").read_text())
    except Exception:
        s = {}
    for x in s.get("longs", [])[:CAPS["tactical_long"]]:
        add(x["ticker"], "tactical_long", px=x["px"], score=x["score"],
            sector=x["sector"], mom12=x["raw"]["mom_12_1_pct"])
    for x in s.get("shorts", [])[:CAPS["tactical_short"]]:
        add(x["ticker"], "tactical_short", px=x["px"], score=x["score"],
            sector=x["sector"])
    n_new = 0
    for side in ("longs", "shorts"):
        for x in s.get(side, []):
            if x.get("new_entrant") and n_new < CAPS["new_entrant"]:
                add(x["ticker"], "new_entrant", px=x["px"], score=x["score"])
                n_new += 1

    # fundamental/event generators
    f, fundamental_meta = compute_scores()
    if len(f):
        pead = f.dropna(subset=["pead"]) if "pead" in f else f.iloc[0:0]
        for t, r in pead.reindex(pead.pead.abs()
                                 .sort_values(ascending=False).index) \
                        .head(CAPS["pead_fresh"]).iterrows():
            if abs(r.pead) >= 1.0:
                add(t, "pead_fresh", sue=round(float(r.sue), 2),
                    days_since_report=int(r.days_since_report))
        if "est_revision" in f:
            for t, v in f.est_revision.dropna().sort_values(
                    ascending=False).head(CAPS["revision_leader"]).items():
                add(t, "revision_leader", est_revision=round(float(v), 2))
        try:
            vq = f[["value", "quality"]].dropna()
            v70, q70 = vq.value.quantile(0.7), vq.quality.quantile(0.7)
            joint = vq[(vq.value > v70) & (vq.quality > q70)]
            for t, r in joint.sort_values("value", ascending=False) \
                             .head(CAPS["cheap_quality"]).iterrows():
                add(t, "cheap_quality", value=round(float(r.value), 2),
                    quality=round(float(r.quality), 2))
        except Exception:
            pass
        if "squeeze_flag" in f:
            for t in list(f.index[f.squeeze_flag == True])[:CAPS["squeeze_flag"]]:  # noqa: E712
                add(t, "squeeze_flag",
                    short_pct_float=round(float(f.loc[t, "short_pct_float"]), 1))

    # insider clusters
    try:
        cl = json.loads((RESEARCH_DIR / "positioning" /
                         "insider_clusters.json").read_text())
        for c in cl.get("clusters", [])[:CAPS["insider_cluster"]]:
            add(c["ticker"], "insider_cluster", n_buys=c["n_buys"],
                insider_net_usd=c["net_value_usd"])
    except Exception:
        pass

    # enrich with next-earnings (the kill-test fodder) + dossier existence
    try:
        cal = pd.read_parquet(RESEARCH_DIR / "events" / "earnings_calendar.parquet")
        nxt = dict(zip(cal.ticker, cal.next_earnings))
        for t, e in entries.items():
            if t in nxt:
                e["detail"]["next_earnings"] = nxt[t]
    except Exception:
        pass
    dossiers = RESEARCH_DIR.parent / "knowledge" / "dossiers"
    for t, e in entries.items():
        e["has_dossier"] = (dossiers / t / "facts.json").exists()

    slate = sorted(entries.values(),
                   key=lambda e: (-len(e["buckets"]),
                                  -abs(e["detail"].get("score", 0))))
    signal_scope = {"kind": "liquid_price_cross_section",
                    "n": s.get("n_liquid"), "reference_n": s.get("n_universe"),
                    "market_wide": False}
    fundamental_scope = {"kind": "fundamental_subset", "market_wide": False,
                         **fundamental_meta.get("scope", {})}
    return {"as_of": datetime.now(ET).isoformat(),
            "n": len(slate),
            "confluence": [e["ticker"] for e in slate if len(e["buckets"]) >= 2],
            "slate": slate,
            "generator_scope": {
                "tactical_long": signal_scope, "tactical_short": signal_scope,
                "new_entrant": signal_scope,
                "pead_fresh": fundamental_scope,
                "revision_leader": fundamental_scope,
                "cheap_quality": fundamental_scope,
                "squeeze_flag": fundamental_scope,
                "insider_cluster": {"kind": "recent_SEC_Form4_filings",
                                    "market_wide": False},
            },
            "method": "stratified generators (caps: "
                      + ", ".join(f"{k}={v}" for k, v in CAPS.items())
                      + "); multi-bucket confluence ranks first; "
                        "IPS 1-3-view exit gate unchanged"}


def main() -> int:
    res = build()
    tmp = OUT.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(res, indent=2) + "\n")
    os.replace(tmp, OUT)
    day = datetime.now(ET).date().isoformat()
    shutil.copy(OUT, RESEARCH_DIR / f"candidates_{day}.json")
    print(f"[candidates] {res['n']} names, "
          f"{len(res['confluence'])} multi-bucket: {res['confluence']}")
    print(f"→ {OUT}")
    return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
