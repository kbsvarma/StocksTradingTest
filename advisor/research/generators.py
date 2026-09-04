"""Generator registry — the single source of truth for what a candidate bucket
means, how strong an instance of it is, and which way it points.

WHY THIS EXISTS
---------------
`candidates.py` builds a deliberately stratified slate from ten generators and
its docstring says it exists to replace "read the top-20 momentum list".
`picks.py` then ranked on `detail.score` — a field only the three price-derived
generators ever populated. Every fundamental candidate scored `0.65 * 0 + ...`
and could not clear the top-10 cut:

    best fundamental-only name   0.0875
    worst price-scored name      0.5895
    10th-pick cutoff             0.6024

Measured over 20 archived slates: 346 of 681 candidate entries (50.8%) were
structurally unpickable, and 200 of 200 published picks carried a price-scored
bucket. The stratified slate was collapsing back into the momentum list it was
written to replace — which is why longs and shorts lost near-identically
(-3.00% vs -3.62%): they were the same factor.

THE FIX
-------
Every generator emits a COMMON CURRENCY: `rank_pct` in [0,1], the candidate's
percentile *in the direction the generator implies*, plus an explicit
direction. A top-decile estimate-revision name and a top-decile momentum name
then compete on equal footing.

DESIGN RULES (each one is load-bearing)
  * A generator that cannot state a direction is NOT standalone. It may add
    confluence but may never lead a pick. We do not guess a direction.
  * Confluence counts DISTINCT FAMILIES, not buckets. tactical_long +
    new_entrant + technical_setup are three views of one price series; counting
    them as three rewarded momentum crowding.
  * Priors are neutral (1.0) until there is a real sample, then shrunk toward
    1.0 by count — the same discipline `ic_weight_multipliers` already applies.
  * `rank_basis` records what population a percentile was taken against, so a
    percentile computed over 5 nominated names is never mistaken for one
    computed over 1,400.
"""
from __future__ import annotations

# --- families -------------------------------------------------------------
# Confluence is only meaningful across INDEPENDENT evidence. Two buckets in the
# same family are one piece of evidence seen twice.
FAMILY = {
    "tactical_long": "price_momentum",
    "tactical_short": "price_momentum",
    "new_entrant": "price_momentum",
    "technical_setup": "price_momentum",
    "revision_leader": "fundamental_revision",
    "pead_fresh": "fundamental_revision",
    "cheap_quality": "fundamental_value",
    "edgar_quality_growth": "fundamental_value",
    "insider_cluster": "positioning",
    "squeeze_flag": "positioning",
    "repeat_kill": "kill_memory",
}

# --- direction ------------------------------------------------------------
#   "long" / "short"  — fixed by construction
#   "signed"          — read from the sign of the generator's own metric
#   None              — no directional claim; cannot lead a pick
DIRECTION = {
    "tactical_long": "long",
    "tactical_short": "short",
    "revision_leader": "long",
    "pead_fresh": "signed",
    "cheap_quality": "long",
    "edgar_quality_growth": "long",
    "insider_cluster": "long",
    "new_entrant": None,
    "technical_setup": None,
    "squeeze_flag": None,
    "repeat_kill": "short",
}

# --- standalone -----------------------------------------------------------
# May this generator LEAD a pick on its own?
#   new_entrant     — a modifier on a tactical name, not a thesis
#   technical_setup — candidates.py: "descriptive and research-only ... never
#                     adds a model weight or actionability by itself"
#   squeeze_flag    — high short interest is a catalyst in EITHER direction
#   repeat_kill     — candidates.py: retained for attribution, "NOT injected
#                     into the candidate slate"
STANDALONE = {
    "tactical_long": True,
    "tactical_short": True,
    "revision_leader": True,
    "pead_fresh": True,
    "cheap_quality": True,
    "edgar_quality_growth": True,
    "insider_cluster": True,
    "new_entrant": False,
    "technical_setup": False,
    "squeeze_flag": False,
    "repeat_kill": False,
}

ALL_BUCKETS = tuple(FAMILY)

# --- scoring --------------------------------------------------------------
STRENGTH_W = 0.75        # weight on the lead generator's shrunk percentile
CONFLUENCE_W = 0.25      # weight on independent-family agreement
MAX_FAMILIES = 3         # 3+ independent families saturates the bonus

# prior fitting (pick_tracker supplies the sample; neutral until then)
PRIOR_MIN_N = 30         # below this a bucket gets no prior at all
PRIOR_K = 25.0           # shrinkage: conf = n / (n + K)
PRIOR_LO, PRIOR_HI = 0.60, 1.40


def family(bucket: str) -> str:
    return FAMILY.get(bucket, "other")


def families(buckets) -> list[str]:
    """Distinct families present, order-stable."""
    out = []
    for b in buckets:
        f = family(b)
        if f not in out:
            out.append(f)
    return out


def confluence(buckets) -> float:
    """0.0 for a single family, 0.5 for two, 1.0 for three or more.

    A lone candidate gets ZERO confluence credit. The previous formula
    (`min(len(buckets), 4) / 4`) handed every single-bucket name a free 0.25,
    which is how three price-derived buckets on one ticker outranked genuine
    cross-family agreement.
    """
    n = len(families(buckets))
    if n <= 1:
        return 0.0
    return round(min(n - 1, MAX_FAMILIES - 1) / (MAX_FAMILIES - 1), 6)


def pct_rank(values: dict, ticker: str, higher_is_stronger: bool = True) -> float | None:
    """Percentile of `ticker` within `values` (ticker -> float).

    Returns a value in (0,1]; None when the ticker is absent or the population
    is degenerate. Ties take the average rank, so duplicated metrics do not
    manufacture separation.
    """
    if not values or ticker not in values:
        return None
    v = values.get(ticker)
    if v is None:
        return None
    try:
        target = float(v)
    except (TypeError, ValueError):
        return None
    pool = []
    for x in values.values():
        try:
            fx = float(x)
        except (TypeError, ValueError):
            continue
        if fx == fx:                      # NaN check without importing math
            pool.append(fx)
    n = len(pool)
    if n < 2:
        return None
    below = sum(1 for x in pool if x < target)
    equal = sum(1 for x in pool if x == target)
    r = (below + 0.5 * equal) / n         # midrank, in (0,1)
    return round(r if higher_is_stronger else 1.0 - r, 6)


def resolve_direction(bucket: str, metric_value=None) -> str | None:
    """Direction this generator claims for a candidate, or None."""
    d = DIRECTION.get(bucket)
    if d != "signed":
        return d
    try:
        v = float(metric_value)
    except (TypeError, ValueError):
        return None
    if v == 0 or v != v:
        return None
    return "long" if v > 0 else "short"


def score_candidate(gens: dict, priors: dict | None = None) -> dict | None:
    """Score one candidate from its per-generator evidence.

    `gens` maps bucket -> {"rank_pct": float, "direction": str|None,
                           "metric": str, "value": Any, "rank_basis": str}

    Returns the full selection decomposition, or None when no standalone
    generator with a direction and a percentile is present — in which case the
    candidate is deliberately NOT pickable rather than guessed at.
    """
    priors = priors or {}
    contributions = {}
    lead, lead_strength = None, -1.0
    for b, g in gens.items():
        rp = g.get("rank_pct")
        prior = float(priors.get(b, 1.0))
        eligible = bool(STANDALONE.get(b) and rp is not None and g.get("direction"))
        strength = round(rp * prior, 6) if rp is not None else None
        contributions[b] = {
            "rank_pct": rp, "prior": round(prior, 4), "strength": strength,
            "family": family(b), "standalone": bool(STANDALONE.get(b)),
            "direction": g.get("direction"), "eligible_to_lead": eligible,
            "metric": g.get("metric"), "value": g.get("value"),
            "rank_basis": g.get("rank_basis"),
        }
        if eligible and strength > lead_strength:
            lead, lead_strength = b, strength
    if lead is None:
        return None
    fam = families(gens.keys())
    conf = confluence(gens.keys())
    score = round(STRENGTH_W * lead_strength + CONFLUENCE_W * conf, 6)
    return {
        "score": min(score, 1.0),
        "lead_bucket": lead,
        "lead_strength": round(lead_strength, 6),
        "lead_rank_pct": gens[lead].get("rank_pct"),
        "lead_prior": round(float(priors.get(lead, 1.0)), 4),
        "direction": gens[lead].get("direction"),
        "direction_source": lead,
        "families": fam,
        "n_families": len(fam),
        "confluence": conf,
        "formula": (f"{STRENGTH_W}*lead_strength + {CONFLUENCE_W}*confluence; "
                    "lead_strength = rank_pct * prior; confluence over "
                    "DISTINCT FAMILIES (0 for one family)"),
        "contributions": contributions,
    }


def fit_priors(by_bucket: dict, baseline_hit: float) -> dict:
    """Shrunk multiplicative priors from realized outcomes.

    `by_bucket` maps bucket -> {"n": int, "hit_rate": float}. A bucket below
    PRIOR_MIN_N gets exactly 1.0 — no prior at all, not a weak one. Everything
    else is shrunk toward 1.0 by n/(n+K) and clipped, so a bucket cannot be
    switched off or doubled on a thin sample.
    """
    out = {}
    if not baseline_hit or baseline_hit <= 0:
        return {b: 1.0 for b in by_bucket}
    for b, st in by_bucket.items():
        n = int(st.get("n") or 0)
        hit = st.get("hit_rate")
        if n < PRIOR_MIN_N or hit is None:
            out[b] = 1.0
            continue
        conf = n / (n + PRIOR_K)
        raw = float(hit) / baseline_hit
        raw = max(PRIOR_LO, min(PRIOR_HI, raw))
        out[b] = round(1.0 + conf * (raw - 1.0), 4)
    return out
