from __future__ import annotations

import pandas as pd

from advisor.research.factors_edgar import raw_metrics, score_frames


def _facts(scale: float, growth: float):
    rows = []
    values = {
        "revenue": (100 * scale, 100 * scale * (1 + growth)),
        "op_income": (10 * scale, 14 * scale), "net_income": (8 * scale, 10 * scale),
        "equity": (50 * scale, 55 * scale), "cfo": (12 * scale, 16 * scale),
        "capex": (3 * scale, 3 * scale), "lt_debt": (30 * scale, 25 * scale),
    }
    for concept, pair in values.items():
        for year, value in zip((2024, 2025), pair):
            rows.append({"concept": concept, "form": "10-K", "fp": "FY",
                         "period_end": f"{year}-12-31", "filed": f"{year + 1}-02-15",
                         "value": value})
    return pd.DataFrame(rows)


def test_edgar_metrics_preserve_filing_date_and_compute_cash_quality():
    metrics = raw_metrics(_facts(1, .2))
    assert round(metrics["revenue_growth"], 3) == .2
    assert metrics["fcf_margin"] > 0
    assert metrics["latest_filed"] == "2026-02-15"
    assert metrics["concept_coverage"] == 7


def test_edgar_cross_section_scores_without_vendor_profiles():
    frames = {f"T{i}": _facts(i + 1, .03 * i) for i in range(1, 8)}
    scored = score_frames(frames)
    assert scored.edgar_quality_growth.notna().all()
    assert scored.edgar_growth.idxmax() == "T7"
