"""Independent SEC-filed growth/quality diagnostics for the active universe.

Unlike vendor profile fields, every input retains its filing timestamp.  This
is a candidate generator only; the values are cross-sectional diagnostics and
must earn forward IC before receiving model weight.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR

ET = ZoneInfo("America/New_York")
FACTS = RESEARCH_DIR / "fundamentals" / "edgar_facts"
OUT = RESEARCH_DIR / "edgar_fundamental_latest.json"


def _annual_values(frame, concept: str) -> list[dict]:
    g = frame[(frame.concept == concept) & frame.form.astype(str).str.startswith("10-K")].copy()
    if "fp" in g and (g.fp == "FY").any():
        g = g[g.fp == "FY"]
    if not len(g):
        return []
    g = g.dropna(subset=["period_end", "filed", "value"]).sort_values("filed")
    # Latest restatement for each fiscal period is the value known now.  The
    # filed column is preserved for provenance and future PIT reconstruction.
    g = g.groupby("period_end", as_index=False).tail(1).sort_values("period_end")
    return g[["value", "period_end", "filed"]].tail(2).to_dict("records")


def raw_metrics(frame) -> dict:
    vals = {c: _annual_values(frame, c) for c in
            ("revenue", "net_income", "equity", "op_income", "cfo", "capex", "lt_debt")}
    latest = {k: (v[-1]["value"] if v else None) for k, v in vals.items()}
    revenue = latest["revenue"]
    def ratio(a, b):
        try:
            return float(a) / float(b) if a is not None and b not in (None, 0) else None
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    revenue_growth = None
    if len(vals["revenue"]) == 2:
        revenue_growth = ratio(vals["revenue"][-1]["value"], vals["revenue"][-2]["value"])
        revenue_growth = revenue_growth - 1 if revenue_growth is not None else None
    cfo, capex = latest["cfo"], latest["capex"]
    fcf = (float(cfo) - abs(float(capex))) if cfo is not None and capex is not None else None
    filed = max((row["filed"] for rows in vals.values() for row in rows), default=None)
    return {
        "revenue_growth": revenue_growth,
        "operating_margin": ratio(latest["op_income"], revenue),
        "roe": ratio(latest["net_income"], latest["equity"]),
        "fcf_margin": ratio(fcf, revenue),
        "debt_to_cfo": ratio(latest["lt_debt"], abs(float(cfo))) if cfo is not None else None,
        "latest_filed": filed,
        "concept_coverage": sum(v is not None for v in latest.values()),
    }


def score_frames(frames: dict[str, object]):
    import numpy as np
    import pandas as pd
    raw = pd.DataFrame({ticker: raw_metrics(frame) for ticker, frame in frames.items()}).T
    numeric = ["revenue_growth", "operating_margin", "roe", "fcf_margin", "debt_to_cfo"]
    for col in numeric:
        raw[col] = pd.to_numeric(raw[col], errors="coerce")
        lo, hi = raw[col].quantile([.01, .99]) if raw[col].notna().sum() >= 4 else (-np.inf, np.inf)
        raw[col] = raw[col].clip(lo, hi)
    def z(col):
        s = raw[col]
        sd = s.std()
        return ((s - s.mean()) / sd).clip(-3, 3) if sd and not np.isnan(sd) else s * np.nan
    raw["edgar_growth"] = z("revenue_growth")
    raw["edgar_quality"] = pd.concat([
        z("operating_margin"), z("roe"), z("fcf_margin"), -z("debt_to_cfo")], axis=1).mean(axis=1)
    raw["edgar_quality_growth"] = raw[["edgar_growth", "edgar_quality"]].mean(axis=1)
    return raw


def build() -> dict:
    import pandas as pd
    frames = {}
    for path in sorted(FACTS.glob("*.parquet")):
        try:
            frames[path.stem] = pd.read_parquet(path)
        except Exception:
            continue
    scored = score_frames(frames) if frames else pd.DataFrame()
    eligible = scored[(scored.concept_coverage >= 5) & scored.edgar_quality_growth.notna()] \
        if len(scored) else scored
    leaders = []
    for ticker, row in eligible.sort_values("edgar_quality_growth", ascending=False).head(20).iterrows():
        leaders.append({"ticker": ticker,
                        **{k: (round(float(row[k]), 3) if row[k] == row[k] else None)
                           for k in ("edgar_quality_growth", "edgar_growth", "edgar_quality",
                                     "revenue_growth", "operating_margin", "roe", "fcf_margin",
                                     "debt_to_cfo")},
                        "latest_filed": row.get("latest_filed"),
                        "concept_coverage": int(row["concept_coverage"])})
    return {"schema_version": 1, "as_of": datetime.now(ET).isoformat(),
            "source": "SEC EDGAR Company Facts (as-filed; latest annual periods)",
            "n_files": len(frames), "n_eligible": len(eligible), "leaders": leaders,
            "scored": [{"ticker": ticker, "edgar_quality_growth": float(row.edgar_quality_growth)}
                       for ticker, row in eligible.iterrows()],
            "gate": "PROSPECTIVE/DISCOVERY-ONLY — zero model weight until forward IC promotion",
            "method": "revenue growth + operating margin + ROE + FCF margin - debt/CFO; winsorized cross-sectional z"}


def write(result: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(f".json.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(result, indent=2, default=str) + "\n")
    os.replace(tmp, OUT)


def main() -> int:
    result = build(); write(result)
    print(f"[edgar-factors] {result['n_eligible']}/{result['n_files']} eligible -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
