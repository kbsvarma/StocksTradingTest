"""Canonical actionability check shared by UI and alerting surfaces."""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _calibration() -> dict:
    data = Path(os.environ.get("ADVISOR_DATA_DIR", REPO / "advisor" / "data"))
    try:
        value = json.loads((data / "research" / "calibration_latest.json").read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def is_actionable(view: dict) -> bool:
    """Fail closed unless the record is bound to the approved calibration."""
    basis = view.get("probability_basis") or {}
    canonical = _calibration()
    return (
        view.get("recommendation_class") == "actionable_idea"
        and view.get("portfolio_context_status") == "verified"
        and basis.get("calibrated") is True
        and basis.get("type") == "empirical_calibration"
        and canonical.get("actionable_probability_allowed") is True
        and bool(basis.get("calibration_id"))
        and basis.get("calibration_id") == canonical.get("calibration_id")
        and basis.get("sample_n") == canonical.get("n_explicit_scored")
    )
