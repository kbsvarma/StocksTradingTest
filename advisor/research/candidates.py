"""Stratified candidate slate — what the morning session actually triages.

Replaces "read the top-20 momentum list" with a ~50-name slate drawn from
EVERY generator, each entry tagged with WHY it's here (source buckets feed
attribution — generators must earn their keep). Multi-bucket confluence
ranks first. The IPS 1-3-view ceiling is untouched: this widens the
funnel's mouth, not its exit.

Buckets: tactical_long/short (price composite) · technical_setup · pead_fresh
· insider_cluster · revision_leader · cheap_quality · new_entrant · squeeze_flag

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
        "new_entrant": 5, "squeeze_flag": 3, "technical_setup": 10,
        "edgar_quality_growth": 5}

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
    from advisor.research import generators as gen
    from advisor.research.factors_fundamental import compute_scores

    entries: dict[str, dict] = {}
    # Per-generator health. A generator that produced nothing must say WHY —
    # silently emitting an empty bucket is how the fundamental half of this
    # slate went dark for a month without anyone noticing.
    health: dict[str, dict] = {b: {"live": False, "n": 0, "reason": "not evaluated"}
                               for b in CAPS}

    def mark(bucket: str, n: int, reason: str | None = None) -> None:
        health[bucket] = {"live": n > 0, "n": n,
                          "reason": reason if n == 0 else None}

    def add(ticker: str, bucket: str, *, rank_pct=None, metric=None,
            value=None, rank_basis=None, **info) -> None:
        e = entries.setdefault(ticker, {"ticker": ticker, "buckets": [],
                                        "detail": {}, "generators": {}})
        if bucket not in e["buckets"]:
            e["buckets"].append(bucket)
        e["generators"][bucket] = {
            "rank_pct": rank_pct,
            "direction": gen.resolve_direction(bucket, value),
            "metric": metric, "value": value, "rank_basis": rank_basis,
        }
        e["detail"].update({k: v for k, v in info.items() if v is not None})

    # price-factor sheets
    try:
        s = json.loads((RESEARCH_DIR / "signals_latest.json").read_text())
    except Exception:
        s = {}
    if not s.get("longs") and not s.get("shorts"):
        for b in ("tactical_long", "tactical_short", "new_entrant"):
            mark(b, 0, "signals_latest.json missing or empty")
    n_long = n_short = 0
    for x in s.get("longs", [])[:CAPS["tactical_long"]]:
        add(x["ticker"], "tactical_long", px=x["px"], score=x["score"],
            rank_pct=x.get("rank_pct"), metric="composite_z", value=x["score"],
            rank_basis=x.get("rank_basis"),
            sector=x["sector"], mom12=x["raw"]["mom_12_1_pct"])
        n_long += 1
    for x in s.get("shorts", [])[:CAPS["tactical_short"]]:
        add(x["ticker"], "tactical_short", px=x["px"], score=x["score"],
            rank_pct=x.get("rank_pct"), metric="composite_z", value=x["score"],
            rank_basis=x.get("rank_basis"), sector=x["sector"])
        n_short += 1
    if s.get("longs") or s.get("shorts"):
        mark("tactical_long", n_long, "no long names on the factor sheet")
        mark("tactical_short", n_short, "no short names on the factor sheet")
    n_new = 0
    for side in ("longs", "shorts"):
        for x in s.get(side, []):
            if x.get("new_entrant") and n_new < CAPS["new_entrant"]:
                add(x["ticker"], "new_entrant", px=x["px"], score=x["score"],
                    rank_pct=x.get("rank_pct"), metric="composite_z",
                    value=x["score"], rank_basis=x.get("rank_basis"))
                n_new += 1
    if s.get("longs") or s.get("shorts"):
        mark("new_entrant", n_new, "no rank changes vs the prior sheet")

    # Multi-dimensional technical confirmation. This is descriptive and
    # research-only; it enriches/stratifies candidates but never adds a model
    # weight or actionability by itself.
    try:
        technical = json.loads((RESEARCH_DIR / "technical_latest.json").read_text())
        setups = technical.get("setups", [])[:CAPS["technical_setup"]]
        conf_pop = {r["ticker"]: r.get("state_confidence") for r in setups
                    if r.get("state_confidence") is not None}
        for row in setups:
            add(row["ticker"], "technical_setup",
                rank_pct=gen.pct_rank(conf_pop, row["ticker"]),
                metric="state_confidence", value=row.get("state_confidence"),
                rank_basis=f"state_confidence percentile within {len(conf_pop)} "
                           "nominated setups (full population not published)",
                setup=row.get("setup"),
                rsi14=row.get("rsi14"), adx14=row.get("adx14"),
                atr14_pct=row.get("atr14_pct"),
                rs_spy_63d_pct=row.get("rs_spy_63d_pct"),
                volume_ratio=row.get("volume_ratio_20_120"),
                drawdown126_pct=row.get("drawdown126_pct"),
                technical_state_confidence=row.get("state_confidence"))
        mark("technical_setup", len(setups),
             f"technical gate: {technical.get('gate', 'unavailable')}")
    except Exception as exc:
        technical = {}
        mark("technical_setup", 0, f"technical_latest.json unreadable: {exc}")

    # Independent filing-derived fundamentals. These do not depend on Yahoo
    # profile data and carry the latest SEC filing date into research.
    try:
        edgar_factors = json.loads((RESEARCH_DIR / "edgar_fundamental_latest.json").read_text())
        leaders = edgar_factors.get("leaders", [])[:CAPS["edgar_quality_growth"]]
        # Prefer the full scored population when the producer publishes it;
        # fall back to the nominated leaders and SAY SO in rank_basis.
        pop_rows = edgar_factors.get("scored") or edgar_factors.get("all") or leaders
        eg_pop = {r["ticker"]: r.get("edgar_quality_growth") for r in pop_rows
                  if r.get("edgar_quality_growth") is not None}
        full = pop_rows is not leaders
        for row in leaders:
            add(row["ticker"], "edgar_quality_growth",
                rank_pct=gen.pct_rank(eg_pop, row["ticker"]),
                metric="edgar_quality_growth",
                value=row.get("edgar_quality_growth"),
                rank_basis=f"edgar_quality_growth percentile within {len(eg_pop)} "
                           + ("scored names" if full else
                              "nominated leaders (full population not published)"),
                edgar_quality_growth=row.get("edgar_quality_growth"),
                edgar_growth=row.get("edgar_growth"),
                edgar_quality=row.get("edgar_quality"),
                latest_sec_filing=row.get("latest_filed"))
        mark("edgar_quality_growth", len(leaders),
             f"edgar gate: {edgar_factors.get('gate', 'unavailable')}")
    except Exception as exc:
        edgar_factors = {}
        mark("edgar_quality_growth", 0,
             f"edgar_fundamental_latest.json unreadable: {exc}")

    # fundamental/event generators
    f, fundamental_meta = compute_scores()
    FUND = ("pead_fresh", "revision_leader", "cheap_quality", "squeeze_flag")
    if not len(f):
        # Name the upstream cause rather than emitting four silent empties.
        iq = fundamental_meta.get("input_quality") or {}
        bad = [k for k, v in iq.items() if not v.get("usable")]
        why = ("fundamental frame empty — unusable inputs: "
               + ", ".join(f"{k} ({(iq[k] or {}).get('reason') or 'fetch not ok'})"
                           for k in bad)) if bad else \
              "fundamental frame empty — no ingest snapshots on disk"
        for b in FUND:
            mark(b, 0, why)
    else:
        pead = f.dropna(subset=["pead"]) if "pead" in f else f.iloc[0:0]
        pead_pop = {t: abs(float(v)) for t, v in pead.pead.items()}
        n = 0
        for t, r in pead.reindex(pead.pead.abs()
                                 .sort_values(ascending=False).index) \
                        .head(CAPS["pead_fresh"]).iterrows():
            if abs(r.pead) >= 1.0:
                # direction rides the SIGN of the drift; strength rides its size
                add(t, "pead_fresh", rank_pct=gen.pct_rank(pead_pop, t),
                    metric="pead", value=round(float(r.pead), 3),
                    rank_basis=f"|pead| percentile within {len(pead_pop)} "
                               "names with a recent report",
                    sue=round(float(r.sue), 2),
                    days_since_report=int(r.days_since_report))
                n += 1
        mark("pead_fresh", n, "no name cleared |pead| >= 1.0")
        if "est_revision" in f:
            rev_pop = {t: float(v) for t, v in f.est_revision.dropna().items()}
            n = 0
            for t, v in f.est_revision.dropna().sort_values(
                    ascending=False).head(CAPS["revision_leader"]).items():
                add(t, "revision_leader", rank_pct=gen.pct_rank(rev_pop, t),
                    metric="est_revision", value=round(float(v), 2),
                    rank_basis=f"est_revision percentile within {len(rev_pop)} "
                               "names with estimate coverage",
                    est_revision=round(float(v), 2))
                n += 1
            mark("revision_leader", n, "no estimate revisions in the frame")
        else:
            mark("revision_leader", 0, "est_revision column absent from the frame")
        try:
            vq = f[["value", "quality"]].dropna()
            val_pop = {t: float(x) for t, x in vq.value.items()}
            qual_pop = {t: float(x) for t, x in vq.quality.items()}
            v70, q70 = vq.value.quantile(0.7), vq.quality.quantile(0.7)
            joint = vq[(vq.value > v70) & (vq.quality > q70)]
            n = 0
            for t, r in joint.sort_values("value", ascending=False) \
                             .head(CAPS["cheap_quality"]).iterrows():
                # joint claim -> the weaker of the two legs carries it
                pv, pq = gen.pct_rank(val_pop, t), gen.pct_rank(qual_pop, t)
                rp = min(pv, pq) if (pv is not None and pq is not None) else None
                add(t, "cheap_quality", rank_pct=rp,
                    metric="min(value_pct, quality_pct)",
                    value=round(float(r.value), 2),
                    rank_basis=f"weaker leg of value/quality percentiles within "
                               f"{len(val_pop)} names scored on both",
                    quality=round(float(r.quality), 2))
                n += 1
            mark("cheap_quality", n, "no name in the joint value+quality 70th pctile")
        except Exception as exc:
            mark("cheap_quality", 0, f"value/quality unavailable: {exc}")
        if "squeeze_flag" in f:
            sq = list(f.index[f.squeeze_flag == True])[:CAPS["squeeze_flag"]]  # noqa: E712
            sq_pop = {t: float(f.loc[t, "short_pct_float"])
                      for t in f.index[f.squeeze_flag == True]  # noqa: E712
                      if f.loc[t, "short_pct_float"] == f.loc[t, "short_pct_float"]}
            for t in sq:
                add(t, "squeeze_flag", rank_pct=gen.pct_rank(sq_pop, t),
                    metric="short_pct_float",
                    value=round(float(f.loc[t, "short_pct_float"]), 1),
                    rank_basis=f"short interest percentile within {len(sq_pop)} "
                               "flagged names",
                    short_pct_float=round(float(f.loc[t, "short_pct_float"]), 1))
            mark("squeeze_flag", len(sq), "no name tripped the squeeze flag")
        else:
            mark("squeeze_flag", 0, "squeeze_flag column absent from the frame")

    # insider clusters
    try:
        cl = json.loads((RESEARCH_DIR / "positioning" /
                         "insider_clusters.json").read_text())
        clusters = cl.get("clusters", [])
        # Rank on the OPPORTUNISTIC value where the routine/opportunistic split
        # is available (Cohen-Malloy-Pomorski: routine trades carry no
        # information), falling back to total while that history accrues.
        def _val(c):
            v = c.get("opportunistic_value_usd")
            return v if v not in (None, 0) else c.get("net_value_usd")
        cl_pop = {c["ticker"]: _val(c) for c in clusters if _val(c) is not None}
        routine = (cl.get("routine_classification") or {})
        basis_note = ("opportunistic" if routine.get("usable") else
                      "total (routine split needs "
                      f"{routine.get('needs_years', '?')}y history)")
        for c in clusters[:CAPS["insider_cluster"]]:
            add(c["ticker"], "insider_cluster",
                rank_pct=gen.pct_rank(cl_pop, c["ticker"]),
                metric="insider_buy_usd", value=_val(c),
                rank_basis=f"{basis_note} open-market insider buying percentile "
                           f"within {len(cl_pop)} detected signals "
                           f"[{c.get('tier', 'cluster')}]",
                n_buys=c["n_buys"], insider_net_usd=c.get("net_value_usd"),
                insider_tier=c.get("tier"),
                insider_n_holders=c.get("n_insiders"),
                insider_top_title=c.get("top_title"),
                insider_plan_share=c.get("plan_10b5_1_share"))
        mark("insider_cluster", min(len(clusters), CAPS["insider_cluster"]),
             "no Form 4 buy clusters detected in the window")
    except Exception as exc:
        mark("insider_cluster", 0, f"insider_clusters.json unreadable: {exc}")

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

    # Attach the complete technical state to every candidate without sending
    # the 1MB full-universe profile through the model context.
    try:
        profiles_doc = json.loads((RESEARCH_DIR / "technical_profiles_latest.json").read_text())
        profiles = {row["ticker"]: row for row in profiles_doc.get("profiles", [])}
        fields = ("setup", "state_confidence", "rsi14", "adx14", "atr14_pct",
                  "macd_hist_pct", "bollinger_z", "donchian55", "trend_stack",
                  "rs_spy_63d_pct", "volume_ratio_20_120", "downside_vol60_ann_pct",
                  "drawdown126_pct")
        for ticker, entry in entries.items():
            if ticker in profiles:
                entry["detail"]["technical"] = {
                    key: profiles[ticker].get(key) for key in fields}
    except Exception:
        pass

    # Score every candidate in the common currency with NEUTRAL priors. This
    # orders the slate; picks.py re-scores with fitted priors. A candidate with
    # no standalone, directional generator gets selection=None and is honestly
    # marked unpickable rather than being handed a guessed direction.
    for e in entries.values():
        e["selection"] = gen.score_candidate(e["generators"])
        e["pickable"] = e["selection"] is not None
    slate = sorted(entries.values(),
                   key=lambda e: (-(e["selection"] or {}).get("score", -1),
                                  -len(gen.families(e["buckets"])),
                                  -abs(e["detail"].get("score", 0))))
    signal_scope = {"kind": "liquid_price_cross_section",
                    "n": s.get("n_liquid"), "reference_n": s.get("n_universe"),
                    "market_wide": False}
    fundamental_scope = {"kind": "fundamental_subset", "market_wide": False,
                         **fundamental_meta.get("scope", {})}
    live = [b for b, h in health.items() if h["live"]]
    dark = {b: h["reason"] for b, h in health.items() if not h["live"]}
    fam_live = sorted({gen.family(b) for b in live})
    return {"as_of": datetime.now(ET).isoformat(),
            "n": len(slate),
            "n_pickable": sum(1 for e in slate if e["pickable"]),
            # Cross-family agreement, not "appears on several price lists".
            "confluence": [e["ticker"] for e in slate
                           if len(gen.families(e["buckets"])) >= 2],
            # THE DIAGNOSTIC LINE. If every pick on a given day is momentum,
            # this says whether that was a judgement or an outage.
            "generator_health": health,
            "generators_live": live,
            "generators_dark": dark,
            "families_live": fam_live,
            "breadth_warning": (
                None if len(fam_live) >= 2 else
                "SINGLE-FAMILY SLATE — every candidate comes from "
                f"{fam_live[0] if fam_live else 'no'} evidence; picks this day "
                "are not diversified and must not be read as such"),
            "slate": slate,
            "generator_scope": {
                "tactical_long": signal_scope, "tactical_short": signal_scope,
                "new_entrant": signal_scope,
                "technical_setup": {"kind": "OHLCV_technical_state",
                                    "market_wide": False,
                                    "gate": technical.get("gate", "unavailable")},
                "edgar_quality_growth": {"kind": "SEC_as_filed_fundamentals",
                                         "market_wide": False,
                                         "gate": edgar_factors.get("gate", "unavailable")},
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
    # tolerate a partial result: main()'s contract is an atomic publish, not a
    # schema (test_candidate_safety stubs build() with a minimal dict).
    conf = res.get("confluence", [])
    print(f"[candidates] {res.get('n', 0)} names "
          f"({res.get('n_pickable', 0)} pickable), "
          f"{len(conf)} cross-family: {conf}")
    print(f"[candidates] live: {', '.join(res.get('generators_live') or []) or 'NONE'}")
    for b, why in (res.get("generators_dark") or {}).items():
        print(f"[candidates]   dark {b}: {why}")
    if res.get("breadth_warning"):
        print(f"[candidates] ** {res['breadth_warning']}")
    print(f"→ {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
