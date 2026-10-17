from __future__ import annotations

import json

from advisor.macro_check import validate


def _valid():
    return {
        "as_of": "2026-09-03T08:00:00-04:00",
        "overnight": [{"fact": "Futures were flat", "url": "https://exchange.test/a",
                       "ts": "2026-09-03T07:55:00-04:00", "primary": True}],
        "calendar": [{"time_et": "08:30", "event": "Release", "consensus": "1.0",
                      "url": "https://agency.test/release",
                      "retrieved": "2026-09-03T07:50:00-04:00", "primary": True}],
        "anomalies": [{"asset": "CL=F", "move": "-2%", "why": "inventory",
                       "url": "https://agency.test/inventory"}],
        "themes_updated": True,
    }


def test_valid_primary_sourced_macro_contract(tmp_path):
    path = tmp_path / "macro.json"
    path.write_text(json.dumps(_valid()))
    errors, _ = validate(path)
    assert errors == []


def test_macro_contract_rejects_unsourced_calendar(tmp_path):
    value = _valid()
    value["calendar"][0].pop("url")
    path = tmp_path / "macro.json"
    path.write_text(json.dumps(value))
    errors, _ = validate(path)
    assert any("source HTTPS URL required" in error for error in errors)
