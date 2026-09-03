"""brief.json validator — run by the morning session AFTER writing brief.json.

Catches malformed structured briefs before the terminal renders them and
before the session claims success. Zero deps, exit 0 = valid.

Checks per view: required keys, numeric levels coherent with direction
(long: stop < target; short: stop > target), evidence URLs http(s),
conviction in {high, medium}. Warns (non-fatal) on missing yf_ticker /
numeric levels — those views can't be exit-watched and must say why.

CLI: python -m advisor.brief_check advisor/data/context/<date>/brief.json
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import yaml

REQUIRED_VIEW_KEYS = ("instrument", "direction", "conviction", "thesis",
                      "entry", "target", "stop")
MIN_REWARD_RISK = 2.0
MIN_EXPECTANCY_R = 0.15
SOURCES = {"factor_long", "factor_short", "factor_shock", "news_loop",
           "insider_cluster", "revision_leader", "macro_thematic",
           "repeat_kill", "user_suggested", "other"}
REPO = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("ADVISOR_DATA_DIR", REPO / "advisor" / "data"))
CALIBRATION = DATA / "research" / "calibration_latest.json"
CONFIG = REPO / "advisor" / "config_advisor.yaml"
IMPERATIVE_PATTERNS = (
    re.compile(r"\b(?:buy|sell|short|cover|exit|enter)\s+"
               r"(?:now|today|immediately|at|between|below|above|on)\b", re.I),
    re.compile(r"\b(?:you should|we recommend|strong buy|strong sell|guaranteed)\b", re.I),
    re.compile(r"\ballocate\s+\$?\d", re.I),
)


def _risk_policy() -> dict:
    try:
        cfg = yaml.safe_load(CONFIG.read_text()) or {}
        policy = cfg.get("research_risk") or {}
        budget = float(policy["budget_usd"])
        loss_fraction = float(policy["max_view_loss_fraction"])
        max_views = int(policy["max_new_views_per_brief"])
        if budget <= 0 or not 0 < loss_fraction <= 0.25 or not 1 <= max_views <= 10:
            raise ValueError("unsafe research risk limits")
        return {"budget_usd": budget,
                "max_view_loss_usd": budget * loss_fraction,
                "max_new_views_per_brief": max_views}
    except Exception as exc:
        raise ValueError(f"research risk policy unavailable: {type(exc).__name__}") from exc


def _existing_open_capital(exclude_decision_keys: set[str]) -> float:
    from advisor.research.outcomes import open_views
    total = 0.0
    for view in open_views():
        if view.get("decision_key") in exclude_decision_keys:
            continue  # publication retry: the same immutable decision is not new exposure
        capital = (view.get("sizing") or {}).get("capital_usd")
        if _finite_number(capital):
            total += float(capital)
    return total


def _canonical_calibration() -> dict:
    try:
        value = json.loads(CALIBRATION.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _finite_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _unsafe_research_language(view: dict) -> str | None:
    fields = [view.get("thesis"), view.get("entry"), view.get("target"),
              view.get("stop"), view.get("catalyst", {}).get("mechanism")
              if isinstance(view.get("catalyst"), dict) else None]
    fields.extend(view.get("disconfirmers") or [])
    text = " ".join(str(value or "") for value in fields)
    for pattern in IMPERATIVE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def _production_contract(v: dict, tag: str) -> list[str]:
    errs: list[str] = []
    try:
        policy = _risk_policy()
    except ValueError as exc:
        return [f"{tag}: {exc}"]
    account_budget_usd = policy["budget_usd"]
    max_view_loss_usd = policy["max_view_loss_usd"]
    for key in ("decision_key", "data_as_of", "catalyst", "disconfirmers", "reward_risk",
                "expected_value_r", "sizing", "probability_basis",
                "recommendation_class"):
        if key not in v:
            errs.append(f"{tag}: production contract missing '{key}'")
    data_date = None
    try:
        data_date = date.fromisoformat(str(v.get("data_as_of", ""))[:10])
    except ValueError:
        errs.append(f"{tag}: data_as_of must begin with ISO date")
    decision_key = v.get("decision_key")
    if not isinstance(decision_key, str) or len(decision_key) > 120 \
            or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789:_.-" \
                   for ch in decision_key):
        errs.append(f"{tag}: decision_key must be a stable URL-safe identifier")
    catalyst = v.get("catalyst")
    if not isinstance(catalyst, dict) or not catalyst.get("event") \
            or not catalyst.get("date") or not catalyst.get("mechanism"):
        errs.append(f"{tag}: catalyst requires event/date/mechanism")
    else:
        try:
            catalyst_date = date.fromisoformat(str(catalyst["date"])[:10])
            time_stop = date.fromisoformat(str(v.get("time_stop", ""))[:10])
            if catalyst_date > time_stop:
                errs.append(f"{tag}: catalyst occurs after time_stop")
        except ValueError:
            errs.append(f"{tag}: catalyst.date and time_stop must be ISO dates")
    if not isinstance(v.get("disconfirmers"), list) or len(v["disconfirmers"]) < 2 \
            or not all(isinstance(x, str) and x.strip() for x in v["disconfirmers"]):
        errs.append(f"{tag}: at least two concrete disconfirmers required")

    rr, ev = v.get("reward_risk"), v.get("expected_value_r")
    p = v.get("p_win")
    probability = v.get("probability_basis")
    rec_class = v.get("recommendation_class")
    if rec_class not in ("research_idea", "actionable_idea"):
        errs.append(f"{tag}: recommendation_class must be research_idea|actionable_idea")
    if rec_class == "research_idea":
        unsafe = _unsafe_research_language(v)
        if unsafe:
            errs.append(f"{tag}: research_idea contains imperative transaction language "
                        f"({unsafe!r})")
    if not isinstance(probability, dict):
        errs.append(f"{tag}: probability_basis must be an object")
    else:
        kind = probability.get("type")
        calibrated = probability.get("calibrated")
        sample_n = probability.get("sample_n")
        if kind not in ("analyst_judgment", "empirical_calibration"):
            errs.append(f"{tag}: probability_basis.type is invalid")
        if not isinstance(calibrated, bool):
            errs.append(f"{tag}: probability_basis.calibrated must be boolean")
        if not isinstance(sample_n, int) or isinstance(sample_n, bool) or sample_n < 0:
            errs.append(f"{tag}: probability_basis.sample_n must be a nonnegative integer")
        if calibrated and (kind != "empirical_calibration" or not isinstance(sample_n, int)
                           or sample_n < 30):
            errs.append(f"{tag}: calibrated probabilities require empirical_calibration "
                        "with sample_n >= 30")
        if rec_class == "actionable_idea" and calibrated is not True:
            errs.append(f"{tag}: actionable_idea requires a calibrated probability")
        if rec_class == "actionable_idea" and calibrated is True:
            canonical = _canonical_calibration()
            if canonical.get("actionable_probability_allowed") is not True:
                errs.append(f"{tag}: canonical calibration has not passed its "
                            "objective gate and explicit human approval")
            if probability.get("calibration_id") != canonical.get("calibration_id") \
                    or not probability.get("calibration_id"):
                errs.append(f"{tag}: probability_basis.calibration_id must bind to "
                            "the approved canonical calibration artifact")
            if sample_n != canonical.get("n_explicit_scored"):
                errs.append(f"{tag}: probability_basis.sample_n must equal canonical "
                            "explicit scored sample")
    if not _finite_number(rr) or rr < MIN_REWARD_RISK:
        errs.append(f"{tag}: reward_risk must be >= {MIN_REWARD_RISK}")
    if not _finite_number(ev) or ev < MIN_EXPECTANCY_R:
        errs.append(f"{tag}: expected_value_r must be >= {MIN_EXPECTANCY_R}")
    if _finite_number(rr) and _finite_number(p) and _finite_number(ev):
        calculated = p * rr - (1 - p)
        if abs(calculated - ev) > 0.03:
            errs.append(f"{tag}: expected_value_r {ev} != p*reward_risk-(1-p) "
                        f"({calculated:.3f})")
    lo, hi, target, stop = (v.get("entry_px_low"), v.get("entry_px_high"),
                            v.get("target_px"), v.get("stop_px"))
    calculated_rr = None
    if all(_finite_number(x) for x in (lo, hi, target, stop)):
        midpoint = (lo + hi) / 2
        if v.get("direction") == "short":
            risk, reward = stop - midpoint, midpoint - target
        else:
            risk, reward = midpoint - stop, target - midpoint
        if risk > 0:
            calculated_rr = reward / risk
            if _finite_number(rr) and abs(calculated_rr - rr) > 0.03:
                errs.append(f"{tag}: reward_risk {rr} != level-derived value "
                            f"{calculated_rr:.3f}")

    sizing = v.get("sizing")
    needed = ("capital_usd", "max_loss_usd", "portfolio_capital_after_usd",
              "quantity", "instrument_type", "slippage_bps", "method")
    if not isinstance(sizing, dict) or any(k not in sizing for k in needed):
        errs.append(f"{tag}: sizing requires {needed}")
    else:
        capital, loss, after = (sizing.get("capital_usd"),
                                sizing.get("max_loss_usd"),
                                sizing.get("portfolio_capital_after_usd"))
        if not _finite_number(capital) or not 0 < capital <= account_budget_usd:
            errs.append(f"{tag}: sizing.capital_usd outside (0,{account_budget_usd}]")
        if not _finite_number(loss) or not 0 < loss <= max_view_loss_usd:
            errs.append(f"{tag}: sizing.max_loss_usd outside (0,{max_view_loss_usd}]")
        if not _finite_number(after) or not 0 < after <= account_budget_usd:
            errs.append(f"{tag}: portfolio_capital_after_usd exceeds budget")
        quantity, slippage = sizing.get("quantity"), sizing.get("slippage_bps")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            errs.append(f"{tag}: sizing.quantity must be a positive whole number")
        if sizing.get("instrument_type") not in ("equity", "etf"):
            errs.append(f"{tag}: sizing.instrument_type must be equity|etf")
        if not _finite_number(slippage) or not 0 <= slippage <= 100:
            errs.append(f"{tag}: sizing.slippage_bps must be in [0,100]")
        if all(_finite_number(x) for x in (capital, lo, hi)) \
                and isinstance(quantity, int) and not isinstance(quantity, bool):
            midpoint = (lo + hi) / 2
            implied_capital = quantity * midpoint
            if abs(capital - implied_capital) > max(1.0, midpoint * 0.02):
                errs.append(f"{tag}: sizing.capital_usd {capital} does not reconcile "
                            f"to quantity*entry_midpoint {implied_capital:.2f}")
            if _finite_number(loss) and _finite_number(stop) \
                    and _finite_number(slippage):
                per_unit_risk = ((midpoint - stop) if v.get("direction") != "short"
                                 else (stop - midpoint))
                min_loss = quantity * per_unit_risk + implied_capital * slippage / 10_000
                if loss + 0.01 < min_loss:
                    errs.append(f"{tag}: sizing.max_loss_usd understates stop+slippage "
                                f"loss {min_loss:.2f}")
    try:
        time_stop = date.fromisoformat(str(v.get("time_stop", ""))[:10])
        if data_date and time_stop < data_date:
            errs.append(f"{tag}: time_stop precedes data_as_of")
    except ValueError:
        errs.append(f"{tag}: time_stop must begin with ISO date")
    return errs


def validate(path: Path) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    try:
        d = json.loads(path.read_text())
    except Exception as exc:
        return [f"unparseable JSON: {exc}"], []
    views = d.get("views")
    if views is None:
        errs.append("missing 'views' (use [] for a no-ideas day)")
        views = []
    try:
        policy = _risk_policy()
    except ValueError as exc:
        errs.append(str(exc))
        policy = None
    if policy and len(views) > policy["max_new_views_per_brief"]:
        errs.append(f"views: {len(views)} exceeds configured maximum "
                    f"{policy['max_new_views_per_brief']} per brief")
    portfolio_context = d.get("portfolio_context")
    if d.get("schema_version") == 3 and views:
        if not isinstance(portfolio_context, dict) or portfolio_context.get("status") \
                not in ("verified", "unavailable"):
            errs.append("brief: portfolio_context.status must be verified|unavailable")
    for i, v in enumerate(views):
        tag = f"views[{i}] ({v.get('instrument', '?')})"
        if d.get("schema_version") != 3:
            errs.append(f"{tag}: actionable views require schema_version 3")
        for k in REQUIRED_VIEW_KEYS:
            if not v.get(k):
                errs.append(f"{tag}: missing '{k}'")
        if v.get("conviction") not in ("high", "medium"):
            errs.append(f"{tag}: conviction must be high|medium")
        if v.get("direction") not in ("long", "short"):
            errs.append(f"{tag}: direction must be long|short")
        tp, sp = v.get("target_px"), v.get("stop_px")
        lo, hi = v.get("entry_px_low"), v.get("entry_px_high")
        if _finite_number(tp) and _finite_number(sp):
            long_ = (v.get("direction") or "long").lower() != "short"
            if long_ and not sp < tp:
                errs.append(f"{tag}: long but stop_px {sp} >= target_px {tp}")
            if not long_ and not sp > tp:
                errs.append(f"{tag}: short but stop_px {sp} <= target_px {tp}")
            if not (_finite_number(lo) and _finite_number(hi)):
                errs.append(f"{tag}: numeric entry_px_low/entry_px_high required")
            elif lo > hi:
                errs.append(f"{tag}: entry_px_low {lo} > entry_px_high {hi}")
            elif long_ and not sp < lo <= hi < tp:
                errs.append(f"{tag}: long levels must satisfy stop < entry_low "
                            f"<= entry_high < target")
            elif not long_ and not tp < lo <= hi < sp:
                errs.append(f"{tag}: short levels must satisfy target < entry_low "
                            f"<= entry_high < stop")
        else:
            errs.append(f"{tag}: finite numeric target_px/stop_px required")
        production = d.get("schema_version") == 3
        if not v.get("yf_ticker"):
            (errs if production else warns).append(f"{tag}: no yf_ticker — unwatchable")
        for j, ev in enumerate(v.get("evidence", [])):
            url = ev.get("url", "")
            try:
                parsed = urlsplit(url) if url else None
            except (TypeError, ValueError):
                parsed = None
            if url and (parsed is None or parsed.scheme not in ("http", "https")
                        or not parsed.netloc or any(ord(ch) < 32 for ch in url)):
                errs.append(f"{tag}: evidence[{j}] url not an absolute http(s) URL")
            if not ev.get("claim"):
                errs.append(f"{tag}: evidence[{j}] missing claim")
            if not ev.get("retrieved"):
                errs.append(f"{tag}: evidence[{j}] missing retrieved timestamp")
            else:
                try:
                    datetime.fromisoformat(str(ev["retrieved"]).replace("Z", "+00:00"))
                except ValueError:
                    errs.append(f"{tag}: evidence[{j}] retrieved must be ISO timestamp")
            if not isinstance(ev.get("primary"), bool):
                errs.append(f"{tag}: evidence[{j}] primary must be boolean")
        evidence = v.get("evidence") or []
        hosts = {urlsplit(e.get("url", "")).hostname for e in evidence
                 if isinstance(e, dict) and e.get("url")}
        if len(evidence) < 2 or len(hosts - {None}) < 2:
            errs.append(f"{tag}: at least two independent evidence domains required")
        if not any(e.get("primary") is True for e in evidence if isinstance(e, dict)):
            errs.append(f"{tag}: at least one primary-source evidence item required")
        if v.get("source") not in SOURCES:
            (errs if production else warns).append(
                f"{tag}: source must be one of {sorted(SOURCES)}")
        p = v.get("p_win")
        if p is None:
            (errs if production else warns).append(f"{tag}: no 'p_win'")
        elif not (_finite_number(p) and 0.50 <= p <= 0.85):
            errs.append(f"{tag}: p_win must be numeric in [0.50, 0.85], got {p!r}")
        if not v.get("thesis_tags"):
            (errs if production else warns).append(f"{tag}: no 'thesis_tags'")
        if not v.get("time_stop"):
            (errs if production else warns).append(f"{tag}: no 'time_stop'")
        if production:
            errs.extend(_production_contract(v, tag))
            if v.get("recommendation_class") == "actionable_idea" \
                    and (not isinstance(portfolio_context, dict)
                         or portfolio_context.get("status") != "verified"):
                errs.append(f"{tag}: actionable_idea requires verified portfolio context")
    if d.get("schema_version") == 3 and views:
        after_values = [v.get("sizing", {}).get("portfolio_capital_after_usd")
                        for v in views if isinstance(v.get("sizing"), dict)]
        deployed = sum(v.get("sizing", {}).get("capital_usd", 0) for v in views
                       if _finite_number(v.get("sizing", {}).get("capital_usd")))
        keys = {v.get("decision_key") for v in views if v.get("decision_key")}
        try:
            existing = _existing_open_capital(keys)
        except Exception as exc:
            errs.append(f"views: cannot compute existing open exposure: {type(exc).__name__}")
            existing = None
        budget = policy["budget_usd"] if policy else 0
        if policy and existing is not None and existing + deployed > budget:
            errs.append(f"views: existing {existing:.2f} + new {deployed:.2f} "
                        f"exceeds configured budget {budget:.2f}")
        if after_values and all(_finite_number(x) for x in after_values) \
                and len({round(float(x), 2) for x in after_values}) > 1:
            errs.append("views: portfolio_capital_after_usd must be identical across views")
        if after_values and all(_finite_number(x) for x in after_values) \
                and existing is not None:
            expected_after = existing + deployed
            if any(abs(float(x) - expected_after) > 1.0 for x in after_values):
                errs.append("views: portfolio_capital_after_usd does not reconcile to "
                            f"machine-computed existing+new exposure {expected_after:.2f}")
    for i, r in enumerate(d.get("rejected") or []):
        if not r.get("idea"):
            errs.append(f"rejected[{i}]: missing 'idea'")
        if not r.get("killed_by"):
            errs.append(f"rejected[{i}]: missing 'killed_by'")
    # schema v2 blocks (calendar/watchlist/narrative_delta) — light validation
    for i, c in enumerate(d.get("calendar") or []):
        if not c.get("date") or not c.get("event"):
            errs.append(f"calendar[{i}]: needs 'date' and 'event'")
    if d.get("schema_version") == 2:
        if "calendar" not in d:
            warns.append("v2 brief without 'calendar' block")
        if "watchlist" not in d:
            warns.append("v2 brief without 'watchlist' block")
    return errs, warns


def main() -> int:
    paths = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 2
    # --draft: same contract, used by the pipeline on views_draft.json before
    # the red-team stage (reserved for divergence later)
    errs, warns = validate(Path(paths[0]))
    for w in warns:
        print(f"WARN  {w}")
    for e in errs:
        print(f"ERROR {e}")
    print(f"brief_check: {'INVALID — fix before sending' if errs else 'valid'} "
          f"({len(errs)} errors, {len(warns)} warnings)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
