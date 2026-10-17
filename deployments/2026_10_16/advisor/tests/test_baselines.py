"""Regressions for the naive-comparator baselines.

"-8.66pp vs SPY" answers nothing on its own. The load-bearing comparator is
the RANDOM DRAW: same slate, same day, same count, score ignored. If a random
draw does as well as the top-10 by score, the ranking function contributes
nothing and the result belongs to the generators instead.
"""
import json

import pytest

from advisor.research import pick_tracker as pt


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    def _write(rows):
        p = tmp_path / "picks_ledger.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        monkeypatch.setattr(pt, "LEDGER", p)
    return _write


def _res(pid, ret, day="2026-08-01", bench=None, unstopped=None):
    r = {"type": "resolution", "id": pid, "status": "resolved",
         "return_pct": ret, "date": day, "ticker": pid}
    if bench is not None:
        r["bench_return_pct"] = bench
    if unstopped is not None:
        r["unstopped_return_pct"] = unstopped
    return r


def test_no_resolved_picks_is_reported_not_crashed(ledger):
    ledger([])
    assert pt.baselines()["usable"] is False


def test_beats_spy_is_computed_on_identical_windows(ledger):
    ledger([_res("A", 5.0, bench=2.0), _res("B", 3.0, bench=2.0)])
    b = pt.baselines()
    assert b["picks_mean_return_pct"] == pytest.approx(4.0)
    assert b["spy_mean_return_pct"] == pytest.approx(2.0)
    assert b["vs_spy_pp"] == pytest.approx(2.0)


def test_random_draw_detects_a_ranking_that_adds_nothing(ledger):
    """All picks identical -> a random draw must match exactly, and the
    p-value must be 1.0: the ranking cannot possibly be adding anything."""
    ledger([_res(f"T{i}", 2.0, day="2026-08-01") for i in range(10)])
    b = pt.baselines(n_boot=300)
    assert b["vs_random_draw_pp"] is None
    assert b["p_random_beats_ranking"] is None
    assert b["ranking_comparison_usable"] is False


def test_random_draw_control_uses_the_same_day_opportunity_set(ledger):
    ledger([_res("A", 10.0, day="2026-08-01"), _res("B", 10.0, day="2026-08-01"),
            _res("C", -10.0, day="2026-08-02"), _res("D", -10.0, day="2026-08-02")])
    b = pt.baselines(n_boot=200)
    # every draw takes one from each day -> mean is always 0
    assert b["random_draw_mean_pct"] is None


def test_stop_cost_separates_exit_policy_from_signal(ledger):
    ledger([_res("A", -3.0, unstopped=1.0), _res("B", -1.0, unstopped=1.0)])
    b = pt.baselines()
    assert b["unstopped_mean_return_pct"] == pytest.approx(1.0)
    assert b["stop_cost_pp"] == pytest.approx(-3.0)   # the stop cost 3pp


def test_baselines_are_deterministic_for_a_fixed_seed(ledger):
    ledger([_res(f"T{i}", float(i), day=f"2026-08-{i+1:02d}") for i in range(8)])
    assert pt.baselines(n_boot=200)["random_draw_mean_pct"] == \
           pt.baselines(n_boot=200)["random_draw_mean_pct"]
