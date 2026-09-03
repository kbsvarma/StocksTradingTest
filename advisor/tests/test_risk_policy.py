from __future__ import annotations

import json

import advisor.brief_check as brief_check


def _minimal_view(capital: float, after: float, key="2026-09-03:ABC:long"):
    return {"decision_key": key, "instrument": "ABC", "yf_ticker": "ABC",
            "direction": "long", "conviction": "high", "thesis": "x",
            "entry": "100", "entry_px_low": 100.0, "entry_px_high": 100.0,
            "target": "120", "target_px": 120.0, "stop": "90", "stop_px": 90.0,
            "sizing": {"capital_usd": capital,
                       "portfolio_capital_after_usd": after}}


def test_existing_plus_new_exposure_is_machine_reconciled(tmp_path, monkeypatch):
    path = tmp_path / "brief.json"
    path.write_text(json.dumps({"schema_version": 3,
                                "portfolio_context": {"status": "unavailable"},
                                "views": [_minimal_view(2_000, 26_000)]}))
    monkeypatch.setattr(brief_check, "_production_contract", lambda *args: [])
    monkeypatch.setattr(brief_check, "_existing_open_capital", lambda keys: 24_000)
    errors, _ = brief_check.validate(path)
    assert any("existing 24000.00 + new 2000.00" in e for e in errors)


def test_model_cannot_fabricate_capital_after_number(tmp_path, monkeypatch):
    path = tmp_path / "brief.json"
    path.write_text(json.dumps({"schema_version": 3,
                                "portfolio_context": {"status": "unavailable"},
                                "views": [_minimal_view(2_000, 2_000)]}))
    monkeypatch.setattr(brief_check, "_production_contract", lambda *args: [])
    monkeypatch.setattr(brief_check, "_existing_open_capital", lambda keys: 5_000)
    errors, _ = brief_check.validate(path)
    assert any("does not reconcile" in e and "7000.00" in e for e in errors)


def test_missing_risk_policy_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(brief_check, "CONFIG", tmp_path / "missing.yaml")
    try:
        brief_check._risk_policy()
    except ValueError as exc:
        assert "research risk policy unavailable" in str(exc)
    else:
        raise AssertionError("missing policy was accepted")
