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
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


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
    """Path-aware resolution of every still-open pick whose horizon allows."""
    import pandas as pd
    from advisor.research.datastore import load_panel

    close, high, low = load_panel("close"), load_panel("high"), load_panel("low")
    counts = {"resolved": 0, "still_open": 0, "ambiguous": 0, "skipped": 0}
    resolutions = []

    for pid, p in effective().items():
        if p.get("status") != "open":
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
        h, l, c = high[t].iloc[window], low[t].iloc[window], close[t].iloc[window]
        h, l, c = h.dropna(), l.dropna(), c.dropna()
        if not len(c):
            counts["still_open"] += 1
            continue

        long_ = p.get("direction", "long") != "short"
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

        row = {"type": "resolution", "id": pid, "ticker": t,
               "ts": datetime.now(ET).isoformat(),
               "status": "resolved", "outcome": outcome,
               "exit_px": round(exit_px, 2), "return_pct": ret_pct,
               "r_multiple": r_mult,
               "days_to_outcome": (hit_day + 1) if hit_day is not None else horizon,
               "win": None if outcome == "ambiguous" else win,
               "score": p.get("score"),
               "calibration_eligible": outcome != "ambiguous"}
        resolutions.append(row)
        counts["ambiguous" if outcome == "ambiguous" else "resolved"] += 1

    if resolutions:
        with LEDGER.open("a", encoding="utf-8") as f:
            for r in resolutions:
                f.write(json.dumps(r) + "\n")
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
    scored = [r for r in effective().values()
              if r.get("status") == "resolved" and r.get("calibration_eligible")
              and r.get("win") is not None and r.get("score") is not None
              and int(r.get("scoring_version") or 1) == SCORING_VERSION]
    n = len(scored)
    raw = []
    for lo, hi in BUCKETS:
        sub = [r for r in scored if lo <= r["score"] < hi]
        if len(sub) >= MIN_BUCKET_N:
            raw.append((lo, hi, sum(r["win"] for r in sub) / len(sub), len(sub)))
    usable = n >= MIN_N_CALIBRATE and len(raw) >= 2
    smoothed = _pav(raw) if usable else raw
    buckets = [{"lo": lo, "hi": hi, "hit_rate": round(rate, 4), "n": bn}
               for lo, hi, rate, bn in smoothed]
    payload = {"schema_version": 2, "fitted_at": datetime.now(ET).isoformat(),
               "scoring_version": SCORING_VERSION,
               "n_resolved": n, "min_n": MIN_N_CALIBRATE,
               "usable": usable, "buckets": buckets,
               "method": ("empirical hit rate per score bucket, "
                          "pool-adjacent-violators monotone smoothing; "
                          f"fitted ONLY on scoring_version={SCORING_VERSION} picks"),
               "gate": (f"auto-applied at n>={MIN_N_CALIBRATE}"
                        if usable else
                        f"NOT applied — {n}/{MIN_N_CALIBRATE} resolved "
                        f"scoring_version={SCORING_VERSION} picks")}
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


def fit_generator_priors(verbose: bool = True) -> dict:
    """Fit per-generator priors from realized outcomes. Neutral until earned.

    Only picks carrying a lead_bucket count. Below generators.PRIOR_MIN_N a
    bucket gets exactly 1.0 — no prior at all rather than a weak one — and
    above it the ratio is shrunk by n/(n+K) and clipped, so no generator can be
    switched off or doubled on a thin sample.
    """
    from advisor.research import generators as gen
    from advisor.research.picks import PRIORS
    att = attribution()
    cells = {k: v for k, v in att["by_lead_bucket"].items()
             if k in gen.ALL_BUCKETS and v.get("hit_rate") is not None}
    base = att["overall"].get("hit_rate")
    priors = gen.fit_priors(cells, base) if base else {}
    active = {k: v for k, v in priors.items() if v != 1.0}
    payload = {
        "schema_version": 1, "fitted_at": datetime.now(ET).isoformat(),
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


def stats() -> dict:
    st = effective().values()
    res = [r for r in st if r.get("status") == "resolved"]
    dec = [r for r in res if r.get("win") is not None]
    wins = sum(r["win"] for r in dec)
    rs = [r["r_multiple"] for r in res if r.get("r_multiple") is not None]
    by_outcome: dict[str, int] = {}
    for r in res:
        by_outcome[r.get("outcome", "?")] = by_outcome.get(r.get("outcome", "?"), 0) + 1
    return {"as_of": datetime.now(ET).isoformat(),
            "total_picks": len(st),
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

    st = stats()
    close = load_panel("close")
    idx = close.index
    picks_r, bench_r = [], []
    for p in effective().values():
        if not (p.get("ref_px") and p.get("price_bar") and p.get("ticker")):
            continue
        t = p["ticker"]
        if t not in close.columns:
            continue
        s0 = idx.searchsorted(pd.Timestamp(p["price_bar"]), side="right")
        horizon = int(p.get("horizon_td", 21))
        if s0 + horizon >= len(idx) or s0 < 1:
            continue
        a, b = float(close[t].iloc[s0 - 1]), float(close[t].iloc[s0 + horizon - 1])
        if not (a > 0 and b > 0):
            continue
        sign = 1 if p.get("direction", "long") != "short" else -1
        picks_r.append(sign * (b / a - 1) * 100)
        if "SPY" in close.columns:
            sa, sb = float(close["SPY"].iloc[s0 - 1]), float(close["SPY"].iloc[s0 + horizon - 1])
            bench_r.append((sb / sa - 1) * 100)

    mean_pick = sum(picks_r) / len(picks_r) if picks_r else None
    mean_bench = sum(bench_r) / len(bench_r) if bench_r else None
    vs_spy = round(mean_pick - mean_bench, 2) if (mean_pick is not None
                                                  and mean_bench is not None) else None
    n_ind = max(1, len(set(p.get("date") for p in effective().values()
                           if p.get("date"))) // 21)
    if vs_spy is None:
        verdict = "insufficient data to benchmark"
    elif vs_spy < 0:
        verdict = (
            f"NO EDGE IN THIS SAMPLE. Directional return {mean_pick:+.2f}% vs SPY "
            f"{mean_bench:+.2f}% over {st['resolved']} resolved picks — "
            f"{vs_spy:+.1f}pp. Stops are not the cause: the gap holds with no stop "
            f"applied. Caveat: these windows overlap and span roughly {n_ind} "
            f"independent period(s) in one regime (the Jul-Aug momentum unwind), "
            f"so this is absence of evidence for an edge, not proof of a "
            f"permanent one against.")
    else:
        verdict = (f"Outperformed SPY by {vs_spy:+.1f}pp over {st['resolved']} "
                   f"picks, but only ~{n_ind} independent period(s) — not yet "
                   f"significant.")

    rec = {**st, "horizon_td": 21,
           "mean_pick_return_pct": round(mean_pick, 2) if mean_pick is not None else None,
           "mean_spy_return_pct": round(mean_bench, 2) if mean_bench is not None else None,
           "vs_spy_pp": vs_spy, "independent_periods": n_ind,
           "verdict": verdict,
           # which generator produced which outcome — the record that turns
           # "picks lost money" into "this generator lost money"
           "attribution": attribution()}
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
