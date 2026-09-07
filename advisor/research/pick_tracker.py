"""Follow every daily pick to conclusion, then auto-calibrate confidence.

Resolution is deterministic and path-aware: walk the daily bars from the day
after issue to the horizon and decide which level the price reached FIRST.
Using only the final close would score a pick that got stopped out on day 3
and recovered by day 21 as a winner — that is the classic backtest lie.

Ambiguity rule: if a single day's high and low straddle BOTH the stop and the
target, the daily bar cannot say which came first, so the pick resolves
`ambiguous` and is excluded from calibration rather than guessed.

Calibration: empirical hit rate per score bucket, monotone-smoothed (pooled
adjacent violators) so higher score never maps to lower probability. Auto-
applied above MIN_N with every refit versioned and logged — probabilities are
low-risk to recalibrate; factor WEIGHTS remain user-gated as before.

CLI:
  python -m advisor.research.pick_tracker --resolve     # follow open picks
  python -m advisor.research.pick_tracker --calibrate   # refit the mapping
  python -m advisor.research.pick_tracker --stats
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR
from advisor.research.picks import CALIBRATION, LEDGER

ET = ZoneInfo("America/New_York")
MIN_N_CALIBRATE = 40          # below this the mapping is noise
MIN_BUCKET_N = 8
BUCKETS = [(0.0, 0.25), (0.25, 0.40), (0.40, 0.55), (0.55, 0.70), (0.70, 1.01)]


def _rows() -> list[dict]:
    from advisor.research.suggestion_store import read_rows, committed_ids
    rows = read_rows(LEDGER)
    research = LEDGER.parent / "research"
    committed = committed_ids(research)
    result = [r for r in rows if not r.get("release_id") or r["release_id"] in committed]
    # Immutable release issue rows are authoritative, even if the legacy
    # journal mirror was lost. Append only missing issues, before resolutions.
    present = {r.get("id") for r in result if r.get("type") == "pick"}
    recovered = []
    for release in sorted(committed):
        doc = json.loads((research / "suggestion_releases" / f"{release}.json").read_text())
        for row in doc.get("issue_rows", []):
            if row["id"] not in present:
                recovered.append({**row, "release_id": release}); present.add(row["id"])
    return recovered + result


def effective() -> dict[str, dict]:
    """Latest state per pick id (resolution rows supersede the open row)."""
    state: dict[str, dict] = {}
    for r in _rows():
        pid = r.get("id")
        if not pid:
            continue
        state[pid] = {**state.get(pid, {}), **r}
    return state


def resolve(verbose: bool = True) -> dict:
    from advisor.research.suggestion_store import writer_lock
    from advisor.research.datastore import pinned_build
    with writer_lock(LEDGER.parent / "research"), pinned_build():
        return _resolve(verbose)


def _resolve(verbose: bool = True) -> dict:
    """Path-aware resolution of every still-open pick whose horizon allows."""
    import pandas as pd
    from advisor.research.datastore import load_panel

    close, high, low = load_panel("close"), load_panel("high"), load_panel("low")
    counts = {"resolved": 0, "still_open": 0, "ambiguous": 0, "skipped": 0}
    resolutions = []

    try:
        opens = load_panel("open")
    except (OSError, KeyError):
        opens = None
    for pid, p in effective().items():
        needs_comparator = p.get("unstopped_return_pct") is None
        if p.get("status") != "open" and not needs_comparator:
            continue
        t, issued = p.get("ticker"), p.get("price_bar")
        if t not in close.columns or not issued:
            counts["skipped"] += 1
            continue
        idx = close.index
        try:
            start = idx.searchsorted(pd.Timestamp(issued), side="right")
        except Exception:
            counts["skipped"] += 1
            continue
        horizon = int(p.get("horizon_td", 21))
        window = slice(start, start + horizon)
        # Preserve identical dates across OHLC. Independent dropna shifts bars
        # against one another and can manufacture target/stop hits.
        if t not in high.columns or t not in low.columns:
            counts["skipped"] += 1
            continue
        bars = pd.concat({"h": high[t].reindex(idx).iloc[window],
                          "l": low[t].reindex(idx).iloc[window],
                          "c": close[t].iloc[window]}, axis=1)
        if bars.isna().any().any():
            counts["skipped"] += 1
            continue
        h, l, c = bars["h"], bars["l"], bars["c"]
        if not len(c):
            counts["still_open"] += 1
            continue

        long_ = p.get("direction", "long") != "short"
        matured = (start + horizon) <= len(idx)
        if p.get("status") != "open":
            if matured and needs_comparator:
                resolutions.append({"type": "comparator", "id": pid,
                    "unstopped_return_pct": round((1 if long_ else -1) * (float(c.iloc[-1]) / float(p["ref_px"]) - 1) * 100, 2)})
            continue
        if p.get("outcome_policy") == "entry_band_daily_v1":
            from advisor.research.entry_simulation import simulate
            import pandas as pd
            # A morning issue may use today's forward bar; an intraday issue
            # may not use the already elapsed portion of that bar.
            issue = pd.Timestamp(p["issued_ts"])
            forward = pd.DataFrame({"high": h, "low": l, "close": c})
            if opens is not None and t in opens:
                forward["open"] = opens[t].reindex(forward.index)
            if issue.tzinfo is None:
                counts["skipped"] += 1
                continue
            local_issue = issue.tz_convert(ET)
            first_allowed = local_issue.date()
            if (local_issue.hour, local_issue.minute) >= (9, 30):
                from datetime import timedelta
                first_allowed += timedelta(days=1)
            forward = forward[[d.date() >= first_allowed for d in forward.index]]
            if forward.empty:
                counts["still_open"] += 1
                continue
            sim = simulate(p, forward, matured=matured)
            if sim.get("status") == "resolved":
                sim.update({"type": "resolution", "id": pid, "ts": datetime.now(ET).isoformat(),
                            "unstopped_return_pct": round((1 if long_ else -1) * (float(c.iloc[-1]) / float(p["ref_px"]) - 1) * 100, 2) if matured else None})
                resolutions.append(sim)
                counts["ambiguous" if sim.get("outcome") == "ambiguous" else "resolved"] += 1
            else:
                counts["still_open"] += 1
            continue
        stop, target = float(p["stop"]), float(p["target"])
        hit_day = None
        outcome = None
        for i in range(len(c)):
            day_hi, day_lo = float(h.iloc[i]), float(l.iloc[i])
            tgt_hit = day_hi >= target if long_ else day_lo <= target
            stp_hit = day_lo <= stop if long_ else day_hi >= stop
            if tgt_hit and stp_hit:
                outcome, hit_day = "ambiguous", i      # same bar: unknowable
                break
            if tgt_hit:
                outcome, hit_day = "target", i
                break
            if stp_hit:
                outcome, hit_day = "stop", i
                break
        matured = (start + horizon) <= len(idx)
        if outcome is None and not matured:
            counts["still_open"] += 1
            continue
        if outcome is None:
            outcome = "expired"

        ref = float(p["ref_px"])
        exit_px = (target if outcome == "target" else
                   stop if outcome == "stop" else float(c.iloc[-1]))
        sign = 1 if long_ else -1
        ret_pct = round(sign * (exit_px / ref - 1) * 100, 2)
        risk = abs(ref - stop)
        r_mult = round(sign * (exit_px - ref) / risk, 3) if risk else None
        win = 1 if outcome == "target" else 0 if outcome == "stop" else (
            1 if (ret_pct or 0) > 0 else 0)

        # SIGNAL QUALITY AND EXIT POLICY MUST BE SEPARABLE. A 1.5xATR(20) stop
        # over a 21-day horizon is about a third of one standard deviation of
        # the horizon's own move (sqrt(21) ~ 4.6 daily ATRs), and 72.6% of
        # picks stopped. With only the stopped return recorded, "is the signal
        # predictive" and "is the exit rule sane" are one confounded question.
        # The unstopped return is the same pick held blindly to the horizon.
        unstopped = None
        if matured or len(c) >= horizon:
            unstopped = round(sign * (float(c.iloc[-1]) / ref - 1) * 100, 2)
        # benchmark the same window so excess return needs no later join
        bench_ret = None
        if "SPY" in close.columns:
            # Same reference date and actual exit date as the pick, not a
            # truncated first-forward-bar to full-horizon comparison.
            end = start + (hit_day if hit_day is not None else len(c) - 1)
            if start > 0 and end < len(idx):
                base, final = close["SPY"].iloc[start - 1], close["SPY"].iloc[end]
                if pd.notna(base) and pd.notna(final) and base > 0:
                    bench_ret = round((float(final) / float(base) - 1) * 100, 2)

        row = {"type": "resolution", "id": pid, "ticker": t,
               "ts": datetime.now(ET).isoformat(),
               "status": "resolved", "outcome": outcome,
               "exit_px": round(exit_px, 2), "return_pct": ret_pct,
               "unstopped_return_pct": unstopped,
               "bench_return_pct": bench_ret,
               "r_multiple": r_mult,
               "days_to_outcome": (hit_day + 1) if hit_day is not None else horizon,
               "win": None if outcome == "ambiguous" else win,
               "score": p.get("score"),
               "calibration_eligible": outcome != "ambiguous"}
        resolutions.append(row)
        counts["ambiguous" if outcome == "ambiguous" else "resolved"] += 1

    if resolutions:
        from advisor.research.suggestion_store import append_rows
        append_rows(LEDGER, resolutions)
    if verbose:
        print(f"pick_tracker resolve: {counts}")
    return counts


def _pav(points: list[tuple]) -> list[tuple]:
    """Pool-adjacent-violators: enforce non-decreasing hit rate across buckets."""
    out = [[lo, hi, rate, n] for lo, hi, rate, n in points]
    i = 0
    while i < len(out) - 1:
        if out[i][2] > out[i + 1][2]:
            a, b = out[i], out[i + 1]
            tot = a[3] + b[3]
            merged = [a[0], b[1], (a[2] * a[3] + b[2] * b[3]) / tot, tot]
            out[i:i + 2] = [merged]
            i = max(0, i - 1)
        else:
            i += 1
    return [tuple(x) for x in out]


def calibrate(verbose: bool = True) -> dict:
    from advisor.research.picks import SCORING_VERSION
    # A calibration maps SCORE -> hit rate. Scores from different scoring
    # versions are not the same quantity, so pooling them would fabricate a
    # mapping for a system that never produced those outcomes. Rows predating
    # the field are v1 by definition.
    population = _independent_episodes([r for r in effective().values()
                  if int(r.get("scoring_version") or 1) == SCORING_VERSION
                  and r.get("source") == "live"])
    scored = [r for r in population if r.get("status") == "resolved"
              and r.get("calibration_eligible") is True
              and r.get("win") is not None and r.get("score") is not None]
    n = len(scored)
    raw = []
    for lo, hi in BUCKETS:
        sub = [r for r in scored if lo <= r["score"] < hi]
        if len(sub) >= MIN_BUCKET_N:
            raw.append((lo, hi, sum(r["win"] for r in sub) / len(sub), len(sub)))
    # In-sample hit rates are descriptive, not validated probabilities.
    # Require a separate time-held-out evaluation before promotion.
    usable = False
    smoothed = _pav(raw) if usable else raw
    buckets = [{"lo": lo, "hi": hi, "hit_rate": round(rate, 4), "n": bn}
               for lo, hi, rate, bn in smoothed]
    payload = {"schema_version": 2, "fitted_at": datetime.now(ET).isoformat(),
               "scoring_version": SCORING_VERSION,
               "n_resolved": n, "min_n": MIN_N_CALIBRATE,
               "usable": usable, "buckets": buckets,
               "probability_gate": "blocked_pending_time_held_out_validation",
               "method": ("empirical hit rate per score bucket, "
                          "pool-adjacent-violators monotone smoothing; "
                          f"fitted ONLY on scoring_version={SCORING_VERSION} picks"),
               "gate": (f"auto-applied at n>={MIN_N_CALIBRATE}"
                        if usable else
                        f"NOT applied — {n} live resolved v{SCORING_VERSION} picks; "
                        "independent time-held-out probability validation required")}
    payload["calibration_id"] = hashlib.sha256(
        json.dumps(buckets, sort_keys=True).encode()).hexdigest()
    tmp = CALIBRATION.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, CALIBRATION)
    if verbose:
        print(f"pick_tracker calibrate: n={n} usable={usable}")
        for b in buckets:
            print(f"  score {b['lo']:.2f}-{b['hi']:.2f}: "
                  f"hit {b['hit_rate']*100:.1f}% (n={b['n']})")
    return payload


def _independent_episodes(rows):
    first = {}
    for row in sorted(rows, key=lambda r: (r.get("issued_ts", ""), r.get("id", ""))):
        key = row.get("episode_id") or row.get("id")
        if key is not None:
            first.setdefault(key, row)
    return list(first.values())


def _agg(rows: list) -> dict:
    """Outcome summary for one attribution cell."""
    dec = [r for r in rows if r.get("win") is not None]
    rets = [r["return_pct"] for r in rows
            if isinstance(r.get("return_pct"), (int, float))]
    rs = [r["r_multiple"] for r in rows
          if isinstance(r.get("r_multiple"), (int, float))]
    return {
        "n": len(rows),
        "hit_rate": round(sum(r["win"] for r in dec) / len(dec), 4) if dec else None,
        "mean_return_pct": round(sum(rets) / len(rets), 3) if rets else None,
        "mean_r": round(sum(rs) / len(rs), 3) if rs else None,
    }


def attribution() -> dict:
    """Outcomes cut by WHICH GENERATOR led the pick.

    This is the point of the whole attribution chain: when picks lose money the
    answer should name a generator, not shrug at "the model". A generator whose
    cell is persistently negative is a design decision to revisit; one that is
    positive earns weight through fit_generator_priors().
    """
    from advisor.research import generators as gen
    rows = [r for r in effective().values() if r.get("status") == "resolved"]
    by_lead: dict[str, list] = {}
    by_family: dict[str, list] = {}
    by_nfam: dict[str, list] = {}
    by_version: dict[str, list] = {}
    for r in rows:
        lead = r.get("lead_bucket") or "unattributed_v1"
        by_lead.setdefault(lead, []).append(r)
        by_family.setdefault(gen.family(lead) if r.get("lead_bucket") else "unattributed_v1",
                             []).append(r)
        by_nfam.setdefault(str(r.get("n_families") or "?"), []).append(r)
        by_version.setdefault(str(r.get("scoring_version") or 1), []).append(r)
    return {
        "as_of": datetime.now(ET).isoformat(),
        "n_resolved": len(rows),
        "overall": _agg(rows),
        "by_lead_bucket": {k: _agg(v) for k, v in
                           sorted(by_lead.items(), key=lambda kv: -len(kv[1]))},
        "by_lead_family": {k: _agg(v) for k, v in
                           sorted(by_family.items(), key=lambda kv: -len(kv[1]))},
        "by_n_families": {k: _agg(v) for k, v in sorted(by_nfam.items())},
        "by_scoring_version": {k: _agg(v) for k, v in sorted(by_version.items())},
        "note": ("'unattributed_v1' are picks made before per-generator "
                 "attribution existed; they cannot be assigned a lead generator "
                 "retrospectively and are reported separately, never pooled."),
    }


def baselines(n_boot: int = 2000, seed: int = 7, rows=None) -> dict:
    """Naive comparators. "-8.66pp vs SPY" answers nothing without them.

    Four questions, in increasing order of how much they hurt:
      1. Did the picks beat the market?            (SPY on identical windows)
      2. Did they beat holding the whole slate?    (equal-weight all picks)
      3. Did the RANKING add anything?             (random draw from the slate)
      4. Did the exit rule help or hurt?           (unstopped vs stopped)

    (3) is the one that matters most. If a random draw from the same slate
    does as well as the top-10 by score, the ranking function contributes
    nothing and every result belongs to the generators instead.
    """
    import random

    rows = [r for r in (effective().values() if rows is None else rows)
            if r.get("status") == "resolved"
            and isinstance(r.get("return_pct"), (int, float))]
    if not rows:
        return {"usable": False, "reason": "no resolved picks"}

    picked = [r["return_pct"] for r in rows]
    bench = [r["bench_return_pct"] for r in rows
             if isinstance(r.get("bench_return_pct"), (int, float))]
    unstopped = [r["unstopped_return_pct"] for r in rows
                 if isinstance(r.get("unstopped_return_pct"), (int, float))]

    # The selected-pick ledger is not the opportunity set. Bootstrapping it
    # cannot measure selection lift; withhold this comparator until complete
    # point-in-time candidate outcomes (including unselected names) exist.
    draws = []
    mean_pick = sum(picked) / len(picked)
    rand_mean = (sum(draws) / len(draws)) if draws else None
    # one-sided: how often does a random draw beat the actual ranking?
    beat = (sum(1 for d in draws if d >= mean_pick) / len(draws)) if draws else None

    def _m(v):
        return round(sum(v) / len(v), 3) if v else None

    return {
        "usable": True,
        "n_resolved": len(rows),
        "picks_mean_return_pct": round(mean_pick, 3),
        "spy_mean_return_pct": _m(bench),
        "vs_spy_pp": (_m([r["return_pct"] - r["bench_return_pct"] for r in rows
                          if isinstance(r.get("bench_return_pct"), (int, float))])),
        "n_spy_paired": len(bench),
        "equal_weight_slate_pct": None,
        "ranking_comparison_usable": False,
        "random_draw_mean_pct": (round(rand_mean, 3) if rand_mean is not None
                                 else None),
        "vs_random_draw_pp": (round(mean_pick - rand_mean, 3)
                              if rand_mean is not None else None),
        "p_random_beats_ranking": (round(beat, 4) if beat is not None else None),
        "unstopped_mean_return_pct": _m(unstopped),
        "stop_cost_pp": _m([r["return_pct"] - r["unstopped_return_pct"] for r in rows
                            if isinstance(r.get("unstopped_return_pct"), (int, float))]),
        "n_stop_paired": len(unstopped),
        "n_random_draws": len(draws),
        "note": ("Ranking comparison unavailable: full issue-time opportunity-set "
                 "outcomes are required; selected-pick resampling is not a valid "
                 "control. SPY and stop comparisons use matched observations only."),
    }


def fit_generator_priors(verbose: bool = True) -> dict:
    """Fit per-generator priors from realized outcomes. Neutral until earned.

    Only picks carrying a lead_bucket count. Below generators.PRIOR_MIN_N a
    bucket gets exactly 1.0 — no prior at all rather than a weak one — and
    above it the ratio is shrunk by n/(n+K) and clipped, so no generator can be
    switched off or doubled on a thin sample.
    """
    from advisor.research import generators as gen
    from advisor.research.picks import PRIORS, SCORING_VERSION
    # Learning may use only observed live outcomes from this scoring policy.
    # Attribution's all-history totals intentionally include historical replays
    # for reporting, but they must never feed back into the live policy.
    population = _independent_episodes([r for r in effective().values()
                  if r.get("source") == "live" and r.get("scoring_version") == SCORING_VERSION])
    live = [r for r in population if r.get("status") == "resolved"
            and r.get("win") is not None and r.get("calibration_eligible") is True]
    # Use disjoint issue windows. Multiple tickers issued together are one
    # sampling cluster, not independent confirmations of generator skill.
    groups = {}
    for row in live:
        if row.get("date") and row.get("expires_on"):
            groups.setdefault(row["date"], []).append(row)
    selected, last_end = [], ""
    for day, group in sorted(groups.items()):
        if day > last_end:
            selected.extend(group)
            last_end = max(r["expires_on"] for r in group)
    live = selected
    grouped = {}
    for row in live:
        bucket = row.get("lead_bucket")
        if bucket in gen.ALL_BUCKETS:
            grouped.setdefault(bucket, []).append(row)
    def cluster_cell(rows):
        days = {}
        for row in rows:
            days.setdefault(row["date"], []).append(row)
        cells = [_agg(group) for group in days.values()]
        return {"n": len(cells), "n_rows": len(rows),
                "hit_rate": sum(c["hit_rate"] for c in cells) / len(cells) if cells else None,
                "mean_return_pct": _agg(rows)["mean_return_pct"]}
    cells = {key: cluster_cell(rows) for key, rows in grouped.items()}
    base = cluster_cell(live).get("hit_rate")
    priors = gen.fit_priors(cells, base) if base else {}
    active = {k: v for k, v in priors.items() if v != 1.0}
    payload = {
        "schema_version": 1, "fitted_at": datetime.now(ET).isoformat(),
        "source": "live", "scoring_version": SCORING_VERSION,
        "n_live_resolved": len(live),
        "n_nonoverlapping_issue_windows": len({r["date"] for r in live}),
        "sampling_unit": "nonoverlapping issue-date clusters; first revision per episode",
        "baseline_hit_rate": base,
        "min_n": gen.PRIOR_MIN_N, "shrinkage_k": gen.PRIOR_K,
        "clip": [gen.PRIOR_LO, gen.PRIOR_HI],
        "priors": priors, "n_active": len(active),
        "evidence": cells,
        "policy": ("multiplicative on the lead generator's percentile; neutral "
                   f"below n={gen.PRIOR_MIN_N}; shrunk by n/(n+{gen.PRIOR_K:.0f}) "
                   "and clipped — a generator can be leaned away from or toward, "
                   "never switched off or doubled"),
    }
    PRIORS.parent.mkdir(parents=True, exist_ok=True)
    tmp = PRIORS.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, PRIORS)
    if verbose:
        print(f"generator priors: baseline hit {base}, {len(active)} active")
        for b, st in cells.items():
            print(f"  {b:22} n={st['n']:3d} hit={st['hit_rate']} "
                  f"ret={st['mean_return_pct']} -> prior {priors.get(b)}")
    return payload


def stats(rows=None) -> dict:
    st = list(effective().values()) if rows is None else list(rows)
    res = [r for r in st if r.get("status") == "resolved"]
    dec = [r for r in res if r.get("win") is not None]
    wins = sum(r["win"] for r in dec)
    rs = [r["r_multiple"] for r in res if r.get("r_multiple") is not None]
    by_outcome: dict[str, int] = {}
    for r in res:
        by_outcome[r.get("outcome", "?")] = by_outcome.get(r.get("outcome", "?"), 0) + 1
    return {"as_of": datetime.now(ET).isoformat(),
            "total_picks": len(st),
            "unique_episodes": len({r.get("episode_id") or r.get("id") for r in st}),
            "unique_issue_dates": len({r.get("date") for r in st}),
            "independence_note": "Daily revisions overlap; row count is not independent sample size",
            "open": sum(1 for r in st if r.get("status") == "open"),
            "resolved": len(res), "decided": len(dec),
            "hit_rate": round(wins / len(dec), 3) if dec else None,
            "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
            "by_outcome": by_outcome,
            "calibration": json.loads(CALIBRATION.read_text()) if CALIBRATION.exists() else None}



def write_record(verbose: bool = True) -> dict:
    """Publish the lane's honest scorecard, benchmarked against SPY.

    A hit rate alone is meaningless — 24% with a 2:1 target could be fine or
    terrible. The comparison that matters is the same-window buy-and-hold
    alternative, so the benchmark is computed on the identical entry dates.
    """
    import pandas as pd
    from advisor.research.datastore import load_panel

    from advisor.research.picks import SCORING_VERSION
    current_rows = [r for r in effective().values() if r.get("source") == "live"
                    and r.get("scoring_version") == SCORING_VERSION]
    st = stats(current_rows)
    close = load_panel("close")
    idx = close.index
    picks_r, bench_r, paired_windows = [], [], []
    for p in _independent_episodes(current_rows):
        if not (p.get("ref_px") and p.get("price_bar") and p.get("ticker")):
            continue
        t = p["ticker"]
        if t not in close.columns:
            continue
        s0 = idx.searchsorted(pd.Timestamp(p["price_bar"]), side="right")
        horizon = int(p.get("horizon_td", 21))
        if s0 + horizon > len(idx) or s0 < 1:
            continue
        a, b = float(close[t].iloc[s0 - 1]), float(close[t].iloc[s0 + horizon - 1])
        from advisor.suggestion_policy import finite
        if not (finite(a) and finite(b) and a > 0 and b > 0) or close[t].iloc[s0 - 1:s0 + horizon].isna().any():
            continue
        sign = 1 if p.get("direction", "long") != "short" else -1
        if "SPY" in close.columns:
            sa, sb = float(close["SPY"].iloc[s0 - 1]), float(close["SPY"].iloc[s0 + horizon - 1])
            if finite(sa) and finite(sb) and sa > 0 and sb > 0:
                picks_r.append(sign * (b / a - 1) * 100)
                bench_r.append((sb / sa - 1) * 100)
                paired_windows.append((s0 - 1, s0 + horizon - 1))

    mean_pick = sum(picks_r) / len(picks_r) if picks_r else None
    mean_bench = sum(bench_r) / len(bench_r) if bench_r else None
    vs_spy = round(mean_pick - mean_bench, 2) if (mean_pick is not None
                                                  and mean_bench is not None) else None
    n_ind, last_end = 0, -1
    for start, end in sorted(paired_windows):
        if start >= last_end:
            n_ind += 1
            last_end = end
    verdict = ("Insufficient data to benchmark" if vs_spy is None else
               f"Gross directional signal return versus SPY: {vs_spy:+.2f}pp. "
               "This is a descriptive sample with overlapping windows; it does not establish tradable alpha.")

    rec = {**st, "horizon_td": "per idea", "source": "live", "scoring_version": SCORING_VERSION,
           "scope": "current live scorer; one issue per episode in signal benchmark",
           "mean_pick_return_pct": round(mean_pick, 2) if mean_pick is not None else None,
           "mean_spy_return_pct": round(mean_bench, 2) if mean_bench is not None else None,
           "vs_spy_pp": vs_spy, "independent_periods": n_ind,
           "verdict": verdict,
           # which generator produced which outcome — the record that turns
           # "picks lost money" into "this generator lost money"
           "attribution": attribution(),
           # good relative to WHAT — the question every result needs
           "baselines": baselines(rows=current_rows)}
    out = RESEARCH_DIR / "pick_record.json"
    tmp = out.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(rec, indent=2, default=str) + "\n")
    os.replace(tmp, out)
    if verbose:
        print(f"pick_record: hit {rec['hit_rate']} avgR {rec['avg_r']} "
              f"vs SPY {vs_spy}pp")
    return rec


def main() -> int:
    args = sys.argv[1:]
    if "--baselines" in args:
        print(json.dumps(baselines(), indent=2))
        return 0
    if "--attribution" in args:
        print(json.dumps(attribution(), indent=2))
        return 0
    if "--priors" in args:
        fit_generator_priors()
        return 0
    if "--resolve" in args:
        resolve()
        calibrate()              # refit score->hit mapping on new outcomes
        fit_generator_priors()   # refit per-generator priors on the same
        write_record()
        return 0
    if "--calibrate" in args:
        calibrate()
        write_record()
        return 0
    print(json.dumps(stats(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
