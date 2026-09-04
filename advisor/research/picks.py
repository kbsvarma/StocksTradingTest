"""Daily ranked picks — deterministic levels, no model tokens.

The existing view pipeline is a veto machine: synthesis proposes, an
independent red-team kills, and most days nothing survives (4 published views
in two months). That is the right bar for high-conviction calls, but it never
produces enough resolved outcomes to calibrate confidence.

This module adds a second, lower-bar lane beside it. Every trading day it
ranks the candidate slate, attaches ATR-derived entry/stop/target levels and a
confidence score, and writes the top N. Volume is the point: ~10 picks/day
resolves into statistically usable calibration in months, not years.

Honesty rules baked in:
  * Levels are DETERMINISTIC (ATR + last close). No model writes a number here.
  * `score` is signal strength in [0,1] — NOT a probability.
  * `confidence_pct` appears only once pick_tracker has fit an empirical
    mapping; until then the field is null and `confidence_basis` says
    "uncalibrated". Never dress a raw score as a probability.
  * Picks are research_idea class. They do not touch the actionable_idea gate.

CLI: python -m advisor.research.picks [--top 10]
Writes advisor/data/research/picks_latest.json (+ dated copy) and appends
open picks to advisor/data/picks_ledger.jsonl.
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
SCORING_VERSION = 2


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
    return doc.get("priors") or {}


def apply_calibration(score: float, cal: dict | None = None) -> tuple:
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
    if cal_version != SCORING_VERSION:
        return None, (f"uncalibrated — calibration was fitted on scoring_version="
                      f"{cal_version}, picks are v{SCORING_VERSION}")
    for b in buckets:
        if b["lo"] <= score <= b["hi"]:
            return round(b["hit_rate"] * 100, 1), (
                f"empirical n={b['n']} (calibration {cal.get('calibration_id','?')[:8]})")
    return None, "uncalibrated"


def build(top_n: int = 10, as_of: str | None = None,
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
    atr = _atr(high, low, close)
    last_close, last_atr = close.iloc[-1], atr.iloc[-1]
    as_of_bar = str(close.index[-1].date())

    entries = slate.get("slate", [])
    if not entries:
        return {"error": "empty slate"}

    # Signal strength in the common currency. Explicitly NOT a probability.
    from advisor.research import generators as gen
    priors = _priors()
    has_v2 = any(e.get("generators") for e in entries)
    version = SCORING_VERSION if has_v2 else 1
    comps = [abs(e.get("detail", {}).get("score", 0) or 0) for e in entries]
    max_comp = max(comps) or 1.0

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
            continue
        px, a = last_close.get(t), last_atr.get(t)
        if not (isinstance(px, (int, float)) and isinstance(a, (int, float))) \
                or not (px > 0 and a > 0):
            continue
        buckets = e.get("buckets", [])
        gens = e.get("generators") or {}
        sel = gen.score_candidate(gens, priors) if gens else (legacy(e) if not has_v2 else None)
        if sel is None:
            # No standalone generator with a direction. We do NOT guess one.
            unpickable.append({"ticker": t, "buckets": buckets,
                               "reason": "no standalone directional generator"})
            continue
        direction = sel["direction"]
        score = round(min(1.0, sel["score"]), 4)
        sign = -1 if direction == "short" else 1
        stop = px - sign * STOP_ATR * a
        target = px + sign * TARGET_ATR * a
        conf_pct, basis = apply_calibration(score)
        scored.append({
            "ticker": t, "direction": direction,
            "score": score,
            "confidence_pct": conf_pct, "confidence_basis": basis,
            "ref_px": round(float(px), 2),
            "entry_low": round(float(px - 0.25 * a), 2),
            "entry_high": round(float(px + 0.25 * a), 2),
            "stop": round(float(stop), 2),
            "target": round(float(target), 2),
            "atr20": round(float(a), 2),
            "rr": round(TARGET_ATR / STOP_ATR, 2),
            "horizon_td": HORIZON_TD,
            "generators": buckets,
            # WHY this name, in full. Every term of the score, the generator
            # that won, the population its percentile was taken against, and
            # where the direction came from — so a failed pick indicts a
            # specific generator rather than "the model".
            "selection": sel,
            "scoring_version": version,
            "sector": e.get("detail", {}).get("sector"),
            "next_earnings": e.get("detail", {}).get("next_earnings"),
        })

    # Modelled round-trip cost, and a screen for targets too small to be worth
    # crossing the spread for. Fails OPEN — an unavailable cost estimate must
    # not silently empty the slate.
    try:
        from advisor.research import costs as cost_mod
        cost_map = cost_mod.estimate([s["ticker"] for s in scored])
    except Exception as exc:
        cost_map, cost_mod = {}, None
        print(f"[picks] cost model unavailable (non-fatal): {exc}")
    uneconomic = []
    priced = []
    for s in scored:
        c = cost_map.get(s["ticker"])
        s["cost"] = c
        verdict = (cost_mod.screen(s["ticker"], s["ref_px"], s["target"], c)
                   if cost_mod else {"ok": True, "reason": "cost model unavailable"})
        s["cost_screen"] = verdict
        (priced if verdict["ok"] else uneconomic).append(s)
    for s in uneconomic:
        print(f"[picks] uneconomic {s['ticker']}: {s['cost_screen']['reason']}")

    priced.sort(key=lambda x: -x["score"])
    picks, dropped = _select(priced, top_n, close)
    now = datetime.now(ET)
    result = {
        "as_of": now.isoformat(),
        "source": source,
        "price_bar": as_of_bar,
        "n_slate": len(entries), "n_picks": len(picks),
        "n_unpickable": len(unpickable), "unpickable": unpickable[:20],
        "n_uneconomic": len(uneconomic),
        "uneconomic": [{"ticker": s["ticker"],
                        "reason": s["cost_screen"].get("reason")}
                       for s in uneconomic[:20]],
        "n_constrained_out": len(dropped),
        "constrained_out": dropped[:20],
        "constraints": {"max_per_sector": MAX_PER_SECTOR,
                        "max_per_lead_generator": MAX_PER_LEAD,
                        "max_pairwise_corr": MAX_PAIR_CORR,
                        "corr_window_td": CORR_WINDOW},
        "horizon_trading_days": HORIZON_TD,
        "levels_method": (f"deterministic: entry ±0.25*ATR20, stop {STOP_ATR}*ATR20, "
                          f"target {TARGET_ATR}*ATR20 (R:R {TARGET_ATR/STOP_ATR:.1f}); "
                          f"no model-authored numbers"),
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
        "calibration": _calibration().get("calibration_id"),
        "class": "research_idea",
        "disclaimer": ("Research ideas, not personalized advice. Levels are "
                       "mechanical; confidence is empirical only when shown."),
        "picks": picks,
    }
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    day = as_of or now.date().isoformat()
    if source == "live":
        tmp = OUT.with_suffix(f".json.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(result, indent=2) + "\n")
        os.replace(tmp, OUT)
    (RESEARCH_DIR / f"picks_{day}.json").write_text(
        json.dumps(result, indent=2) + "\n")
    _append_ledger(picks, now, as_of_bar, day=day, source=source)
    return result


MAX_PER_SECTOR = 3       # ten picks must not be ten semis
MAX_PER_LEAD = 5         # nor ten expressions of one generator
MAX_PAIR_CORR = 0.75     # nor ten names that move together
CORR_WINDOW = 60


def _select(scored: list, top_n: int, close) -> tuple:
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

    tickers = [p["ticker"] for p in scored if p["ticker"] in close.columns]
    corr = None
    if len(tickers) > 1:
        try:
            rets = close[tickers].tail(CORR_WINDOW + 1).pct_change().dropna(how="all")
            if len(rets) >= 20:
                corr = rets.corr()
        except Exception:
            corr = None

    picks, dropped = [], []
    by_sector: dict = {}
    by_lead: dict = {}
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
            for held in picks:
                h = held["ticker"]
                if h not in corr.columns:
                    continue
                c = corr.at[p["ticker"], h]
                if c == c and abs(float(c)) > MAX_PAIR_CORR:
                    clash = (h, round(float(c), 2))
                    break
        if clash:
            dropped.append({**_slim(p),
                            "reason": f"correlation cap: {clash[1]} with {clash[0]} "
                                      f"over {CORR_WINDOW}d (max {MAX_PAIR_CORR})"})
            continue
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


def _append_ledger(picks: list, now: datetime, price_bar: str,
                   day: str | None = None, source: str = "live") -> int:
    """One open row per pick per day — the record that gets resolved later."""
    day = day or now.date().isoformat()
    existing = set()
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("type") == "pick":
                existing.add((r.get("date"), r.get("ticker")))
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with LEDGER.open("a", encoding="utf-8") as f:
        for p in picks:
            if (day, p["ticker"]) in existing:
                continue        # idempotent: a same-day rerun must not double-log
            f.write(json.dumps({
                "type": "pick", "id": f"P-{day}-{p['ticker']}",
                "date": day, "issued_ts": now.isoformat(), "price_bar": price_bar,
                "source": source,
                "ticker": p["ticker"], "direction": p["direction"],
                "score": p["score"], "confidence_pct": p["confidence_pct"],
                "ref_px": p["ref_px"], "stop": p["stop"], "target": p["target"],
                "horizon_td": p["horizon_td"], "generators": p["generators"],
                # attribution keys, denormalized onto the row so outcome
                # analysis never has to re-open the day's slate
                "scoring_version": p.get("scoring_version"),
                "lead_bucket": (p.get("selection") or {}).get("lead_bucket"),
                "lead_rank_pct": (p.get("selection") or {}).get("lead_rank_pct"),
                "lead_prior": (p.get("selection") or {}).get("lead_prior"),
                "families": (p.get("selection") or {}).get("families"),
                "n_families": (p.get("selection") or {}).get("n_families"),
                "direction_source": (p.get("selection") or {}).get("direction_source"),
                "selection": p.get("selection"),
                "status": "open",
            }) + "\n")
            n += 1
    return n


def backfill(top_n: int = 10) -> dict:
    """Replay every archived slate so calibration has data now, not in months."""
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
