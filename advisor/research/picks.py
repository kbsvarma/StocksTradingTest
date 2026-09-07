"""Versioned daily research suggestions with a separate priority-research lane.

Candidate ranks nominate ideas; source, thesis, event and exposure gates decide
which deserve priority review. Neither a score nor priority is a probability.
An empty priority list is valid. Immutable releases bind issue records to the
same inputs displayed by the terminal; replay never enters live learning.

CLI: python -m advisor.research.picks [--top 10]
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR

ET = ZoneInfo("America/New_York")
OUT = RESEARCH_DIR / "picks_latest.json"
LEDGER = RESEARCH_DIR.parent / "picks_ledger.jsonl"
CALIBRATION = RESEARCH_DIR / "pick_calibration.json"

HORIZON_TD = 21          # trading days; matches the IC/validation framework
ATR_WINDOW = 20
STOP_ATR = 1.5           # stop distance in ATR units
TARGET_ATR = 3.0         # target distance -> 2:1 reward:risk by construction

PRIORS = RESEARCH_DIR / "generator_priors.json"

# v1: 0.65*|composite|/max + 0.35*(n_buckets/4). `composite` was populated ONLY
#     by tactical_long/short/new_entrant, so 50.8% of every slate scored ~0.09
#     against a ~0.60 cutoff and could never be picked. 200/200 published picks
#     carried a price bucket; the stratified slate collapsed to a momentum list.
# v2: generators.score_candidate — every generator emits a cross-sectional
#     percentile in its own claimed direction, confluence counts DISTINCT
#     FAMILIES, and the winning generator is recorded on the pick.
# v1 and v2 picks are DIFFERENT SYSTEMS and must never be pooled in a
# calibration; `scoring_version` on every ledger row keeps them separable.
SCORING_VERSION = 3


def _atr(high, low, close, window: int = ATR_WINDOW):
    """True-range average per ticker over the wide panel.

    NOTE: these are wide frames (date x ticker), so the true range must be an
    ELEMENT-WISE max of the three candidate ranges. pd.concat(axis=1).max(axis=1)
    would collapse every ticker into one series per date.
    """
    prev_close = close.shift(1)
    hl = (high - low).abs()
    hc = (high - prev_close).abs()
    lc = (low - prev_close).abs()
    tr = hl.where(hl >= hc, hc)
    tr = tr.where(tr >= lc, lc)
    return tr.rolling(window, min_periods=max(5, window // 2)).mean()


def _calibration() -> dict:
    try:
        return json.loads(CALIBRATION.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _priors() -> dict:
    """Fitted per-generator priors, or {} (== all neutral) when unfitted."""
    try:
        doc = json.loads(PRIORS.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if doc.get("source") != "live" or doc.get("scoring_version") != SCORING_VERSION:
        return {}
    from advisor.research.generators import PRIOR_MIN_N, PRIOR_LO, PRIOR_HI
    from advisor.suggestion_policy import finite
    if doc.get("n_nonoverlapping_issue_windows", 0) < PRIOR_MIN_N:
        return {}
    return {b: value for b, value in (doc.get("priors") or {}).items()
            if finite(value) and PRIOR_LO <= value <= PRIOR_HI
            and ((doc.get("evidence") or {}).get(b) or {}).get("n", 0) >= PRIOR_MIN_N}


def apply_calibration(score: float, cal: dict | None = None,
                      scoring_version: int = SCORING_VERSION) -> tuple:
    """(confidence_pct, basis). None until an empirical mapping exists.

    A calibration maps SCORE -> hit rate, so it is only valid for the scoring
    version that produced those scores. A v1-fitted mapping applied to a v2
    score is a fabricated probability; refuse it rather than display it.
    """
    cal = _calibration() if cal is None else cal
    buckets = cal.get("buckets") or []
    if not cal.get("usable") or not buckets:
        return None, "uncalibrated"
    cal_version = int(cal.get("scoring_version") or 1)
    if cal_version != scoring_version:
        return None, (f"uncalibrated — calibration was fitted on scoring_version="
                      f"{cal_version}, picks are v{scoring_version}")
    if cal.get("probability_gate") != "time_held_out_validated":
        return None, "uncalibrated — independent time-held-out validation required"
    from advisor.suggestion_policy import finite
    if not finite(score) or not 0 <= score <= 1:
        return None, "uncalibrated — invalid score"
    for b in buckets:
        if not all(finite(b.get(k)) for k in ("lo", "hi", "hit_rate", "n")) or not (0 <= b["lo"] < b["hi"] <= 1.01 and 0 <= b["hit_rate"] <= 1 and b["n"] >= 8):
            return None, "uncalibrated — invalid mapping"
        if b["lo"] <= score < b["hi"]:
            return round(b["hit_rate"] * 100, 1), (
                f"empirical n={b['n']} (calibration {cal.get('calibration_id','?')[:8]})")
    return None, "uncalibrated"


def _input_freshness(slate, bar):
    from advisor.suggestion_policy import release_freshness
    from advisor.research.datastore import current_meta
    verdict = release_freshness({"as_of": slate.get("as_of"), "price_bar": bar})
    from advisor.research.suggestion_store import digest
    if not slate.get("release_id") or slate["release_id"] != digest({"slate": slate.get("slate"), "sources": slate.get("source_manifest")}):
        verdict["reasons"].append("Candidate evidence digest missing or mismatched")
    try:
        meta = current_meta()
        if not slate.get("panel_build_id") or slate["panel_build_id"] != meta.get("build_id"):
            verdict["reasons"].append("Candidate/panel release mismatch or missing binding")
        if not (meta.get("quality") or {}).get("ok"):
            verdict["reasons"].append("Panel quality is not verified")
    except Exception:
        verdict["reasons"].append("Panel provenance unavailable")
    verdict["ok"] = not verdict["reasons"]
    return verdict


def build(top_n: int = 10, as_of: str | None = None,
          source: str = "live") -> dict:
    from advisor.research.datastore import pinned_build
    from advisor.research.suggestion_store import writer_lock, atomic_json
    if as_of:
        source = "backfill"
    if source not in ("live", "backfill"):
        raise ValueError("Unsupported suggestion source")
    if not isinstance(top_n, int) or isinstance(top_n, bool) or not 1 <= top_n <= 100:
        raise ValueError("top_n must be between 1 and 100")
    with writer_lock(RESEARCH_DIR), pinned_build():
        try:
            result = _build(top_n, as_of, source)
        except Exception as exc:
            if source == "live" and not as_of:
                atomic_json(RESEARCH_DIR / "suggestion_health.json", {
                    "status": "failed", "as_of": datetime.now(ET).isoformat(),
                    "reason": f"{type(exc).__name__}: {exc}"})
            raise
        if source == "live" and not as_of:
            atomic_json(RESEARCH_DIR / "suggestion_health.json", {
                "status": "failed" if result.get("error") else "degraded" if result.get("n_priority", 0) == 0 else "healthy",
                "as_of": datetime.now(ET).isoformat(), "reason": result.get("error"),
                "release_id": result.get("release_id"), "n_priority": result.get("n_priority", 0)})
        return result


def _build(top_n: int = 10, as_of: str | None = None,
           source: str = "live") -> dict:
    """Rank the slate and attach levels.

    `as_of` (YYYY-MM-DD) replays an ARCHIVED slate for backfill: the price
    panel is truncated to that date so no level can see the future. Backfilled
    rows are labeled `source="backfill"` end-to-end and are reported separately
    from live picks — a replayed track record is not a live one.
    """
    import pandas as pd
    from advisor.research.datastore import load_panel

    slate_p = (RESEARCH_DIR / f"candidates_{as_of}.json" if as_of
               else RESEARCH_DIR / "candidates_latest.json")
    try:
        slate = json.loads(slate_p.read_text())
    except (OSError, json.JSONDecodeError):
        return {"error": f"{slate_p.name} missing"}

    close, high, low = (load_panel("close"), load_panel("high"), load_panel("low"))
    if as_of:
        cut = pd.Timestamp(as_of)
        close, high, low = (close[close.index <= cut], high[high.index <= cut],
                            low[low.index <= cut])
        if len(close) < ATR_WINDOW + 5:
            return {"error": f"insufficient panel history at {as_of}"}
    if close.empty or high.empty or low.empty:
        return {"error": "price panels unavailable"}
    atr = _atr(high, low, close)
    last_close, last_atr = close.iloc[-1], atr.iloc[-1]
    as_of_bar = str(close.index[-1].date())

    freshness = (_input_freshness(slate, as_of_bar) if source == "live" and not as_of
                 else {"ok": False, "reasons": ["Historical replay — not a current suggestion"]})
    if source == "live" and not as_of and not freshness["ok"]:
        return {"error": "; ".join(freshness["reasons"]), "freshness": freshness}
    entries = slate.get("slate", [])

    # Signal strength in the common currency. Explicitly NOT a probability.
    from advisor.research import generators as gen
    # Current learned outcomes did not exist at historical issue time.
    priors = {} if as_of or source != "live" else _priors()
    calibration = {} if as_of or source != "live" else _calibration()
    has_v2 = any(e.get("generators") for e in entries)
    version = (int(slate.get("scoring_version", 2)) if as_of or source != "live" else SCORING_VERSION) if has_v2 else 1
    comps = [abs(e.get("detail", {}).get("score", 0) or 0) for e in entries]
    max_comp = max(comps, default=1.0) or 1.0

    def legacy(e):
        """Pre-generators slates (archived) — reproduced verbatim so replayed
        history stays faithful, and tagged v1 so it never pools with v2."""
        buckets = e.get("buckets", [])
        short = any(b in ("tactical_short", "repeat_kill") for b in buckets)
        comp = abs(e.get("detail", {}).get("score", 0) or 0)
        conf = min(len(buckets), 4) / 4.0
        return {"score": round(min(1.0, 0.65 * (comp / max_comp) + 0.35 * conf), 4),
                "direction": "short" if short else "long",
                "lead_bucket": None, "direction_source": "legacy_bucket_membership",
                "families": gen.families(buckets), "n_families": len(gen.families(buckets)),
                "confluence": conf, "contributions": {},
                "formula": "v1 legacy: 0.65*|composite|/max + 0.35*(n_buckets/4)"}

    scored, unpickable = [], []
    for e in entries:
        t = e["ticker"]
        if t not in close.columns:
            unpickable.append({"ticker": t, "reason": "Price history unavailable"})
            continue
        px, a = last_close.get(t), last_atr.get(t)
        import math
        if not (isinstance(px, (int, float)) and isinstance(a, (int, float))) \
                or not (math.isfinite(px) and math.isfinite(a)) \
                or not (px > 0 and a > 0):
            unpickable.append({"ticker": t, "reason": "Current price or ATR unavailable/invalid"})
            continue
        buckets = e.get("buckets", [])
        gens = e.get("generators") or {}
        sel = gen.score_candidate(gens, priors, scoring_version=version) if gens else (legacy(e) if not has_v2 else None)
        if sel is None:
            # No standalone generator with a direction. We do NOT guess one.
            unpickable.append({"ticker": t, "buckets": buckets,
                               "reason": "no standalone directional generator"})
            continue
        direction = sel["direction"]
        score = round(min(1.0, sel["score"]), 4)
        evidence = {"sources_verified": bool(e.get("sources_verified")),
                    "sources": e.get("sources", {}), "thesis": e.get("underwriting", {})}
        from advisor.research.suggestion_templates import plan_for, expiry_for
        try:
            plan = plan_for(direction, float(px), float(a), evidence)
        except ValueError as exc:
            unpickable.append({"ticker": t, "buckets": buckets, "reason": str(exc)})
            continue
        for field in ("entry_low", "entry_high", "stop", "target"):
            plan[field] = round(plan[field], 2)
        stop, target = plan["stop"], plan["target"]
        geometry = (stop < plan["entry_low"] <= plan["entry_high"] < target if direction == "long"
                    else target < plan["entry_low"] <= plan["entry_high"] < stop)
        if min(stop, target, plan["entry_low"]) <= 0 or not geometry:
            unpickable.append({"ticker": t, "buckets": buckets,
                               "reason": "non-positive mechanical price level"})
            continue
        conf_pct, basis = (apply_calibration(score, calibration, scoring_version=version)
                           if source == "live" and not as_of else
                           (None, "uncalibrated — historical replay"))
        scored.append({
            "ticker": t, "direction": direction,
            "score": score,
            "confidence_pct": conf_pct, "confidence_basis": basis,
            "ref_px": round(float(px), 2),
            "entry_low": round(plan["entry_low"], 2),
            "entry_high": round(plan["entry_high"], 2),
            "stop": round(float(stop), 2),
            "target": round(float(target), 2),
            "atr20": round(float(a), 2),
            "rr": round(abs(target - (plan["entry_low"] + plan["entry_high"]) / 2) / abs(stop - (plan["entry_low"] + plan["entry_high"]) / 2), 2),
            "horizon_td": plan["horizon_td"],
            "generators": buckets,
            # WHY this name, in full. Every term of the score, the generator
            # that won, the population its percentile was taken against, and
            # where the direction came from — so a failed pick indicts a
            # specific generator rather than "the model".
            "selection": sel,
            "scoring_version": version,
            "sector": e.get("detail", {}).get("sector"),
            "next_earnings": e.get("detail", {}).get("next_earnings"),
            "evidence": evidence,
            "template": plan["template"],
            "expires_on": expiry_for(as_of_bar, plan["horizon_td"]),
        })

    # Modelled round-trip cost, and a screen for targets too small to be worth
    # crossing the spread for. Fails OPEN — an unavailable cost estimate must
    # not silently empty the slate.
    try:
        from advisor.research import costs as cost_mod
        # Current costs did not exist at historical issue time.
        cost_map = {} if as_of or source != "live" else cost_mod.estimate([s["ticker"] for s in scored])
    except Exception as exc:
        cost_map, cost_mod = {}, None
        print(f"[picks] cost model unavailable (non-fatal): {exc}")
    uneconomic = []
    priced = []
    for s in scored:
        c = cost_map.get(s["ticker"])
        s["cost"] = c
        executable_reference = s["entry_high"] if s["direction"] == "long" else s["entry_low"]
        verdict = (cost_mod.screen(s["ticker"], executable_reference, s["target"], c)
                   if cost_mod else {"ok": True, "verified": False,
                                     "reason": "cost model unavailable"})
        s["cost_screen"] = verdict
        s["research_warnings"] = ([] if verdict.get("verified") else
                                  ["Trading costs are unverified; economic viability unknown"])
        (priced if verdict["ok"] else uneconomic).append(s)
    for s in uneconomic:
        print(f"[picks] uneconomic {s['ticker']}: {s['cost_screen']['reason']}")

    from advisor.suggestion_policy import assess_pick
    standing = _standing_exposure() if source == "live" and not as_of else []
    for s in priced:
        s["exposure_blockers"] = (["Already represented in a standing research view"]
                                  if any(h.get("ticker") == s["ticker"] for h in standing) else [])
        s["triage"] = assess_pick(s, freshness=freshness, today=datetime.now(ET).date())
    if as_of and version < 3:
        priced.sort(key=lambda x: -x["score"])
    else:
        priced.sort(key=lambda x: (x["triage"]["group"] != "priority_research", -x["score"], x["ticker"]))
    picks, dropped = _select(priced, top_n, close, standing=standing,
                             absolute_correlation=bool(as_of and version < 3))
    previous = json.loads(OUT.read_text()) if OUT.exists() and source == "live" and not as_of else {}
    _identify(picks, previous, as_of or datetime.now(ET).date().isoformat(), source)
    now = datetime.now(ET)
    result = {
        "as_of": now.isoformat(),
        "schema_version": 2,
        "freshness": freshness,
        "candidate_release_id": slate.get("release_id"),
        "panel_build_id": slate.get("panel_build_id"),
        "n_priority": sum(p["triage"]["group"] == "priority_research" for p in picks),
        "exposure_scope": "standing research views and current list; holdings unverified",
        "source": source,
        "replay_contract": ("Archived nominations/scores, neutral priors, current disclosed entry simulation; not an original live record" if as_of else None),
        "price_bar": as_of_bar,
        "n_slate": len(entries), "n_picks": len(picks),
        "n_cost_unverified": sum(not p["cost_screen"].get("verified") for p in picks),
        "n_unpickable": len(unpickable), "unpickable": unpickable[:20],
        "n_uneconomic": len(uneconomic),
        "uneconomic": [{"ticker": s["ticker"],
                        "reason": s["cost_screen"].get("reason")}
                       for s in uneconomic[:20]],
        "n_constrained_out": len(dropped),
        "constrained_out": dropped[:20],
        "constraints": {"max_priority_research": MAX_PRIORITY_RESEARCH,
                        "max_per_sector": MAX_PER_SECTOR,
                        "max_per_lead_generator": MAX_PER_LEAD,
                        "max_pairwise_corr": MAX_PAIR_CORR,
                        "corr_window_td": CORR_WINDOW},
        "horizon_trading_days": HORIZON_TD,
        "levels_method": (f"deterministic: entry ±0.25*ATR20, stop {STOP_ATR}*ATR20, "
                          f"target {TARGET_ATR}*ATR20 (R:R {TARGET_ATR/STOP_ATR:.1f}); "
                          f"reviewed thesis overrides are disclosed per pick"),
        "scoring_version": version,
        "score_method": (picks[0]["selection"]["formula"] if picks else "n/a")
                        + "; signal strength in [0,1], NOT a probability",
        "priors_applied": priors or "neutral (unfitted)",
        # carried straight through from the slate so a day's picks can be read
        # against what was actually generating that day
        "generator_health": slate.get("generator_health"),
        "generators_live": slate.get("generators_live"),
        "generators_dark": slate.get("generators_dark"),
        "families_live": slate.get("families_live"),
        "breadth_warning": slate.get("breadth_warning"),
        "lead_bucket_mix": _mix(picks),
        "calibration": calibration.get("calibration_id"),
        "class": "research_idea",
        "disclaimer": ("Research ideas, not personalized advice. Levels are "
                       "mechanical; confidence is empirical only when shown."),
        "picks": picks,
        "removed_from_daily_list": [{"ticker": p["ticker"], "revision_id": p.get("revision_id"),
                                      "reason": "Not selected in this release; historical evaluation continues"}
                                     for p in previous.get("picks", [])
                                     if p["ticker"] not in {q["ticker"] for q in picks}],
    }
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    day = as_of or now.date().isoformat()
    # Preserve the full pre-selection opportunity set, not just winners.
    # Future ranking controls must evaluate these same issue-time levels.
    import hashlib
    opportunity = {"schema_version": 1, "issued_at": now.isoformat(),
                   "source": source, "price_bar": as_of_bar,
                   "scoring_version": version, "constraints": result["constraints"],
                   "selected": [p["ticker"] for p in picks],
                   "eligible": priced, "unpickable": unpickable,
                   "uneconomic": uneconomic}
    encoded = json.dumps(opportunity, sort_keys=True, allow_nan=False)
    opportunity_id = hashlib.sha256(encoded.encode()).hexdigest()
    archive = RESEARCH_DIR / "pick_opportunities"
    archive.mkdir(parents=True, exist_ok=True)
    from advisor.research.suggestion_store import atomic_json, commit, append_rows
    atomic_json(archive / f"{opportunity_id}.json", opportunity)
    result["opportunity_set_id"] = opportunity_id
    rows = _issue_rows(picks, now, as_of_bar, day, source)
    if source == "live" and not as_of:
        commit(RESEARCH_DIR, OUT, LEDGER, result, rows)
    else:
        append_rows(LEDGER, rows)
    atomic_json(RESEARCH_DIR / f"picks_{day}{'_replay' if as_of or source != 'live' else ''}.json", result)

    return result


MAX_PRIORITY_RESEARCH = 3
MAX_PER_SECTOR = 3       # ten picks must not be ten semis
MAX_PER_LEAD = 5         # nor ten expressions of one generator
MAX_PAIR_CORR = 0.75     # nor ten names that move together
CORR_WINDOW = 60


def _select(scored: list, top_n: int, close, standing=None, absolute_correlation=False) -> tuple:
    """Greedy selection under exposure constraints.

    Taking `sorted(by score)[:10]` places no limit on how concentrated the
    slate is: ten names can share one sector, one generator, or one factor.
    The measured symmetry of the losses (longs -3.00%, shorts -3.62%) is what
    a single undiversified bet looks like from both sides.

    Every rejection records its BINDING constraint, so the slate can say
    "dropped: sector cap, 3 Information Technology already held" instead of
    silently reordering.
    """
    import numpy as np

    standing = standing or []
    tickers = list(dict.fromkeys(p["ticker"] for p in scored + standing if p["ticker"] in close.columns))
    corr = None
    if len(tickers) > 1:
        try:
            rets = close[tickers].tail(CORR_WINDOW + 1).pct_change(fill_method=None).dropna(how="all")
            if len(rets) >= 20:
                corr = rets.corr(min_periods=20)
        except Exception:
            corr = None

    picks, dropped = [], []
    by_sector: dict = {}
    by_lead: dict = {}
    for held in standing:
        sector = held.get("sector") or "unknown"
        by_sector[sector] = by_sector.get(sector, 0) + 1
    for p in scored:
        if len(picks) >= top_n:
            dropped.append({**_slim(p), "reason": "slate full"})
            continue
        sector = p.get("sector") or "unknown"
        lead = (p.get("selection") or {}).get("lead_bucket") or "legacy"
        if by_sector.get(sector, 0) >= MAX_PER_SECTOR:
            dropped.append({**_slim(p), "reason": f"sector cap: {MAX_PER_SECTOR} "
                                                  f"{sector} already held"})
            continue
        if by_lead.get(lead, 0) >= MAX_PER_LEAD:
            dropped.append({**_slim(p), "reason": f"generator cap: {MAX_PER_LEAD} "
                                                  f"led by {lead} already held"})
            continue
        clash = None
        if corr is not None and p["ticker"] in corr.columns:
            for held in picks + standing:
                h = held["ticker"]
                if h not in corr.columns:
                    continue
                c = corr.at[p["ticker"], h]
                sign = (-1 if p.get("direction") == "short" else 1) * (-1 if held.get("direction") == "short" else 1)
                exposure_corr = abs(float(c)) if absolute_correlation else float(c) * sign
                if c == c and exposure_corr > MAX_PAIR_CORR:
                    clash = (h, round(exposure_corr, 2))
                    break
        if clash:
            dropped.append({**_slim(p),
                            "reason": f"correlation cap: {clash[1]} with {clash[0]} "
                                      f"over {CORR_WINDOW}d (max {MAX_PAIR_CORR})"})
            continue
        missing = [h["ticker"] for h in picks + standing
                   if corr is None or h["ticker"] not in corr.columns
                   or p["ticker"] not in corr.columns
                   or not np.isfinite(corr.at[p["ticker"], h["ticker"]])]
        p["exposure_coverage"] = {"missing_correlations": missing,
                                  "holdings_verified": False,
                                  "scope": "standing research and this list"}
        if missing and p.get("triage"):
            p["triage"]["blockers"].append("Correlation coverage unavailable for " + ", ".join(missing))
            p["triage"]["group"] = "watchlist"
        if (p.get("triage") or {}).get("group") == "priority_research" and sum(
                (held.get("triage") or {}).get("group") == "priority_research" for held in picks) >= MAX_PRIORITY_RESEARCH:
            p["triage"]["group"] = "watchlist"
            p["triage"]["blockers"].append(f"Priority research capacity reached ({MAX_PRIORITY_RESEARCH})")
        picks.append(p)
        by_sector[sector] = by_sector.get(sector, 0) + 1
        by_lead[lead] = by_lead.get(lead, 0) + 1
    return picks, dropped


def _slim(p: dict) -> dict:
    return {"ticker": p["ticker"], "score": p["score"],
            "sector": p.get("sector"),
            "lead_bucket": (p.get("selection") or {}).get("lead_bucket")}


def _mix(picks: list) -> dict:
    """Which generator actually led each published pick."""
    out: dict = {}
    for p in picks:
        b = (p.get("selection") or {}).get("lead_bucket") or "legacy"
        out[b] = out.get(b, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _standing_exposure():
    from advisor.journal import effective
    from advisor.suggestion_policy import lifecycle
    try:
        return [{"ticker": v.get("yf_ticker"), "direction": v.get("direction", "long"),
                 "sector": v.get("sector")}
                for v in effective().values() if v.get("type") == "view"
                and v.get("status") == "open" and v.get("yf_ticker")
                and lifecycle(v) not in ("expired", "invalidated", "target_observed")]
    except (OSError, ValueError):
        # Explicitly block priority promotion when exposure cannot be read.
        raise ValueError("Standing research exposure is unreadable")


def _identify(picks, previous, day, source):
    from advisor.research.suggestion_store import digest
    from advisor.suggestion_policy import lifecycle
    old = {p["ticker"]: p for p in previous.get("picks", [])}
    for p in picks:
        prior = old.get(p["ticker"], {})
        same_episode = (prior.get("direction") == p["direction"] and prior.get("expires_on", "") >= day
                        and lifecycle(prior, p["ref_px"]) not in ("invalidated", "target_observed", "expired"))
        episode = prior.get("episode_id") if same_episode else None
        p["episode_id"] = episode or "E-" + digest([source, day, p["ticker"], p["direction"]])[:20]
        material = {k: p.get(k) for k in ("ticker", "direction", "entry_low", "entry_high", "stop", "target", "horizon_td", "scoring_version", "score", "selection", "evidence", "cost", "triage")}
        p["material_fingerprint"] = digest([source, day, material])
        unchanged = prior.get("material_fingerprint") == p["material_fingerprint"]
        p["revision_id"] = prior["revision_id"] if unchanged else "S-" + digest([p["material_fingerprint"], prior.get("revision_id")])[:24]
        p["previous_revision_id"] = prior.get("previous_revision_id") if unchanged else prior.get("revision_id")
        p["change"] = "unchanged" if unchanged else "updated" if same_episode else "new"
        p["why_now"] = (p["evidence"].get("thesis") or {}).get("why_now") or "Quantitative nomination; catalyst thesis requires underwriting"


def _issue_rows(picks, now, price_bar, day, source):
    return [{**p, "type": "pick", "id": p["revision_id"], "date": day,
             "issued_ts": (now.isoformat() if source == "live" else
                           datetime.fromisoformat(price_bar).replace(hour=16, minute=30, tzinfo=ET).isoformat()),
             "price_bar": price_bar, "source": source,
             "lead_bucket": (p.get("selection") or {}).get("lead_bucket"),
             "lead_rank_pct": (p.get("selection") or {}).get("lead_rank_pct"),
             "lead_prior": (p.get("selection") or {}).get("lead_prior"),
             "n_families": (p.get("selection") or {}).get("n_families"),
             "families": (p.get("selection") or {}).get("families"),
             "status": "open", "outcome_policy": "entry_band_daily_v1",
             "calibration_eligible": False} for p in picks if p["change"] != "unchanged"]


def backfill(top_n: int = 10) -> dict:
    """Replay archived nominations for diagnostics; never feed live calibration."""
    dates = sorted(p.stem.split("_", 1)[1]
                   for p in RESEARCH_DIR.glob("candidates_2*.json"))
    done, failed = 0, []
    for d in dates:
        r = build(top_n, as_of=d, source="backfill")
        if "error" in r:
            failed.append((d, r["error"]))
        else:
            done += 1
    print(f"backfill: {done} slates replayed, {len(failed)} skipped")
    for d, e in failed[:5]:
        print(f"  skip {d}: {e}")
    return {"replayed": done, "skipped": len(failed)}


def main() -> int:
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 10
    if "--backfill" in sys.argv:
        backfill(top)
        return 0
    res = build(top)
    if "error" in res:
        print("picks:", res["error"])
        return 1
    print(f"picks: {res['n_picks']} of {res['n_slate']} slate names "
          f"({res['n_unpickable']} unpickable) v{res['scoring_version']} "
          f"(bar {res['price_bar']}, horizon {res['horizon_trading_days']}td)")
    if res.get("generators_dark"):
        for b, why in res["generators_dark"].items():
            print(f"  dark  {b}: {why}")
    if res.get("breadth_warning"):
        print(f"  ** {res['breadth_warning']}")
    print(f"  lead-generator mix: {res.get('lead_bucket_mix')}")
    for p in res["picks"]:
        conf = (f"{p['confidence_pct']}%" if p["confidence_pct"] is not None
                else f"score {p['score']:.2f} (uncal)")
        sel = p.get("selection") or {}
        lead = sel.get("lead_bucket") or "legacy"
        rp = sel.get("lead_rank_pct")
        why = f"{lead}@{rp:.3f}" if isinstance(rp, (int, float)) else lead
        print(f"  {p['ticker']:<6} {p['direction']:<5} {conf:<22} "
              f"entry {p['entry_low']}-{p['entry_high']}  stop {p['stop']}  "
              f"tgt {p['target']}  [{why}, {sel.get('n_families', '?')}fam]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
