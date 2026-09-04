"""Fabrication audit — is the number the model typed the number we computed?

WHY THIS IS THE HEADLINE METRIC
-------------------------------
For an LLM-driven research product the existential risk is fabrication, not
drawdown. One invented earnings figure ends the product. The stated promise is
that every claim is dated, sourced and independently re-verified — but until
now the re-verification was performed BY A MODEL (the red-team stage) and
never measured. "Our red-team checks it" is an architecture claim. A
fabrication rate is a number, and it is the number a buyer or an interviewer
actually asks for.

WHAT IS CHECKED
---------------
The pipeline computes prices, factor scores, panel ids and sector labels
deterministically. The model then writes prose quoting them. Every such quote
is a testable assertion with ground truth already on disk:

    "DELL long — AI-server momentum leader (score 1.99, unchanged)"
    "identical factor panel (panel_build_id 20260903T100152Z) and price ($492.20)"
    "momentum flag at 92.6% of 52w high"

THE RULE THAT MAKES THIS VALID
------------------------------
Audit against the CONTEXT SNAPSHOT, never against the live research artifact.

`advisor/data/research/candidates_<date>.json` is REWRITTEN by any later
rebuild. `advisor/data/context/<date>/candidates.json` is the immutable copy
the model actually read. Checking against the former produces false
accusations: on 2026-09-03 the research artifact had been rebuilt with new
factor weights and showed DELL at 1.09 where the model had written 1.99 —
which looks like fabrication and is not. Against the context snapshot every
quoted score matched exactly.

  verified      the model's number matches the artifact it was given
  contradicted  it does not
  unverifiable  no ground truth for that assertion in the snapshot

    fabrication_rate = contradicted / (verified + contradicted)

Unverifiable claims are EXCLUDED from the denominator and reported
separately. Counting "we could not check it" as fabrication would make the
metric worthless in exactly the direction that flatters nobody.

CLI: python -m advisor.research.fabrication_audit [--date YYYY-MM-DD] [--all]
Writes advisor/data/research/fabrication_audit.json
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR

ET = ZoneInfo("America/New_York")
CONTEXT = RESEARCH_DIR.parent / "context"
OUT = RESEARCH_DIR / "fabrication_audit.json"

PRICE_TOL = 0.011        # quoted to 2dp
SCORE_TOL = 0.011
PCT_TOL = 0.55           # "92.6% of 52w high" — quoted to 1dp

_SCORE_RE = re.compile(r"\bscore\s+(-?\d+\.\d+)", re.I)
_BUILD_RE = re.compile(r"panel_build_id\s+([0-9A-Za-z]+)")
_52W_RE = re.compile(r"(\d{1,3}\.\d)%\s*of\s*52w", re.I)

# PRECISION OVER RECALL. A naive `\$(\d+\.\d{2})` matches EVERY dollar figure
# in the prose and compares each to spot. On 2026-09-03 that flagged CAKE's
# "analyst mean target ($92.67) sits below spot ($108.57)" as a contradiction
# — the target is not the price, and the sentence is entirely correct. That
# single pattern produced 35 of 36 apparent contradictions and a bogus 26%
# rate. A fabrication metric that cries wolf is worse than none.
#
# So a dollar figure is only tested when the prose says it IS the price.
# Anything else is left unverifiable, which shrinks the denominator and keeps
# the rate honest.
_PRICE_RE = re.compile(
    r"\b(?:price|spot|last|close|closed|trading|traded)\b[^.$]{0,24}"
    r"\$\s?(\d[\d,]*\.\d{2})\b", re.I)


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


def ground_truth(ctx_dir) -> dict:
    """Deterministic facts from the snapshot the model was handed."""
    truth = {"by_ticker": {}, "panel_build_id": None, "source": str(ctx_dir)}
    try:
        cand = json.loads((ctx_dir / "candidates.json").read_text())
        for e in cand.get("slate", []):
            d = e.get("detail") or {}
            truth["by_ticker"][e["ticker"]] = {
                "score": d.get("score"), "px": d.get("px"),
                "sector": d.get("sector"),
                "prox_52w_pct": (d.get("technical") or {}).get("rs_spy_63d_pct"),
            }
    except (OSError, json.JSONDecodeError):
        pass
    try:
        sheet = json.loads((ctx_dir / "factor_sheet.json").read_text())
        truth["panel_build_id"] = sheet.get("panel_build_id")
        for side in ("longs", "shorts"):
            for row in sheet.get(side, []):
                t = truth["by_ticker"].setdefault(row["ticker"], {})
                t.setdefault("score", row.get("score"))
                t.setdefault("px", row.get("px"))
                t["prox_52w_pct"] = (row.get("raw") or {}).get("pct_of_52w_high")
    except (OSError, json.JSONDecodeError):
        pass
    return truth


_SCORE_RANGE_RE = re.compile(
    r"\bscore\s+(-?\d+\.\d+)\s*(?:to|-|–|—)\s*(-?\d+\.\d+)", re.I)
_TICKER_RE = re.compile(r"\b[A-Z]{2,5}\b")


def _is_basket(claim_text: str, ticker: str, truth: dict) -> bool:
    """Does this claim describe several names rather than one?

    "Factor SHORT basket (GSHD/KMPR/COIN/CRK) — score -2.2 to -2.4" states a
    RANGE across four tickers. Attributing that scalar to GSHD flagged a
    contradiction where the real score (-2.36) sits inside the stated range.
    A claim naming more than one known ticker cannot be pinned to one of them.
    """
    known = set(truth.get("by_ticker") or {})
    named = {t for t in _TICKER_RE.findall(claim_text or "") if t in known}
    named.discard(None)
    return len(named) > 1


def _check(claim_text: str, ticker: str, truth: dict) -> list[dict]:
    """Every testable numeric assertion in one piece of model prose."""
    facts = (truth["by_ticker"].get(ticker) or {}) if ticker else {}
    out = []
    basket = _is_basket(claim_text, ticker, truth)

    def add(kind, quoted, actual, tol):
        if basket:
            out.append({"kind": kind, "quoted": quoted, "actual": actual,
                        "verdict": "unverifiable",
                        "reason": "claim spans several tickers; cannot be "
                                  "attributed to one"})
            return
        if actual is None:
            out.append({"kind": kind, "quoted": quoted, "actual": None,
                        "verdict": "unverifiable",
                        "reason": "no ground truth in the context snapshot"})
            return
        ok = abs(float(quoted) - float(actual)) <= tol
        out.append({"kind": kind, "quoted": quoted, "actual": actual,
                    "verdict": "verified" if ok else "contradicted",
                    "delta": round(float(quoted) - float(actual), 4)})

    # A stated RANGE is satisfied by any value inside it.
    ranges = list(_SCORE_RANGE_RE.finditer(claim_text))
    for m in ranges:
        lo, hi = sorted((_num(m.group(1)), _num(m.group(2))))
        actual = facts.get("score")
        if basket or actual is None:
            verdict, reason = "unverifiable", (
                "claim spans several tickers" if basket else "no ground truth")
        else:
            inside = lo - SCORE_TOL <= float(actual) <= hi + SCORE_TOL
            verdict = "verified" if inside else "contradicted"
            reason = None
        out.append({"kind": "factor_score_range", "quoted": [lo, hi],
                    "actual": actual, "verdict": verdict, "reason": reason})
    if not ranges:
        for m in _SCORE_RE.finditer(claim_text):
            add("factor_score", _num(m.group(1)), facts.get("score"), SCORE_TOL)
    for m in _PRICE_RE.finditer(claim_text):
        add("price", _num(m.group(1)), facts.get("px"), PRICE_TOL)
    for m in _52W_RE.finditer(claim_text):
        add("pct_of_52w_high", _num(m.group(1)),
            facts.get("prox_52w_pct"), PCT_TOL)
    for m in _BUILD_RE.finditer(claim_text):
        actual = truth.get("panel_build_id")
        out.append({"kind": "panel_build_id", "quoted": m.group(1),
                    "actual": actual,
                    "verdict": ("unverifiable" if not actual else
                                "verified" if m.group(1) == actual
                                else "contradicted")})
    return out


def audit_date(date: str, artifacts: tuple[str, ...] =
               ("views_draft.json", "brief.json")) -> dict:
    """Audit every model-authored claim in one day's brief."""
    ctx_dir = CONTEXT / date
    if not ctx_dir.is_dir():
        return {"date": date, "usable": False, "reason": "no context dir"}
    truth = ground_truth(ctx_dir)
    if not truth["by_ticker"]:
        return {"date": date, "usable": False,
                "reason": "context snapshot has no deterministic artifacts"}

    items = []
    for name in artifacts:
        try:
            doc = json.loads((ctx_dir / name).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for section in ("views", "rejected"):
            for r in (doc.get(section) or []):
                text = " ".join(str(r.get(k) or "") for k in
                                ("idea", "killed_by", "thesis", "catalyst"))
                if text.strip():
                    items.append({"ticker": r.get("yf_ticker") or r.get("ticker"),
                                  "section": section, "text": text,
                                  "artifact": name})

    checks, findings = [], []
    for it in items:
        for c in _check(it["text"], it["ticker"], truth):
            c.update({"ticker": it["ticker"], "section": it["section"],
                      "artifact": it["artifact"]})
            checks.append(c)
            if c["verdict"] == "contradicted":
                findings.append(c)

    verified = sum(1 for c in checks if c["verdict"] == "verified")
    contradicted = len(findings)
    unverifiable = sum(1 for c in checks if c["verdict"] == "unverifiable")
    decidable = verified + contradicted
    # Evidence URLs: schema-enforced today, never resolved. Reported so the
    # gap is visible rather than assumed covered.
    n_evidence = sum(len(r.get("evidence") or [])
                     for name in artifacts
                     for r in _safe_items(ctx_dir / name))
    return {
        "date": date, "usable": True,
        "n_claims_scanned": len(items),
        "n_checks": len(checks),
        "verified": verified, "contradicted": contradicted,
        "unverifiable": unverifiable,
        "fabrication_rate": (round(contradicted / decidable, 4)
                             if decidable else None),
        "decidable_denominator": decidable,
        "n_evidence_items": n_evidence,
        "findings": findings[:25],
        "ground_truth_source": truth["source"],
        "method": ("model-typed numbers matched against the IMMUTABLE context "
                   "snapshot the model was given, never the live research "
                   "artifact (which later rebuilds overwrite). Unverifiable "
                   "claims are excluded from the denominator, not counted as "
                   "fabrication."),
    }


def publication_gate(date: str) -> dict:
    """Raise before publication when frozen context contradicts the draft."""
    result = audit_date(date, artifacts=("brief.pending.json",))
    if not result.get("usable"):
        raise ValueError("pending brief could not be audited against frozen context")
    if result.get("contradicted"):
        raise ValueError(f"pending brief contains {result['contradicted']} numeric "
                         "claim(s) contradicted by frozen context")
    return result


def _safe_items(path):
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return (doc.get("views") or []) + (doc.get("rejected") or [])


def run(dates=None) -> dict:
    """Audit one or many days and publish the running rate."""
    if dates is None:
        dates = sorted(p.name for p in CONTEXT.glob("2*") if p.is_dir())
    days = [audit_date(d) for d in dates]
    usable = [d for d in days if d.get("usable")]
    ver = sum(d["verified"] for d in usable)
    con = sum(d["contradicted"] for d in usable)
    unv = sum(d["unverifiable"] for d in usable)
    dec = ver + con
    payload = {
        "as_of": datetime.now(ET).isoformat(),
        "schema_version": 1,
        "n_days_audited": len(usable),
        "verified": ver, "contradicted": con, "unverifiable": unv,
        "decidable_denominator": dec,
        "FABRICATION_RATE": round(con / dec, 4) if dec else None,
        "evidence_items_total": sum(d["n_evidence_items"] for d in usable),
        "evidence_liveness": ("not exercised — no published view has carried "
                              "an evidence array under the current schema"),
        "days": days,
    }
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, OUT)
    return payload


def main() -> int:
    dates = None
    if "--date" in sys.argv:
        dates = [sys.argv[sys.argv.index("--date") + 1]]
    res = run(dates)
    rate = res["FABRICATION_RATE"]
    print(f"fabrication audit: {res['n_days_audited']} days | "
          f"{res['verified']} verified, {res['contradicted']} contradicted, "
          f"{res['unverifiable']} unverifiable")
    print(f"  FABRICATION RATE: "
          f"{'n/a (nothing decidable)' if rate is None else f'{rate:.2%}'} "
          f"of {res['decidable_denominator']} decidable claims")
    for d in res["days"]:
        if d.get("usable") and d["n_checks"]:
            print(f"    {d['date']}: {d['verified']}v {d['contradicted']}c "
                  f"{d['unverifiable']}u")
    for f in [f for d in res["days"] if d.get("usable") for f in d["findings"]][:10]:
        print(f"  CONTRADICTED {f['ticker']} {f['kind']}: "
              f"model said {f['quoted']}, artifact says {f['actual']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
