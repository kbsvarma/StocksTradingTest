import pytest
from advisor.intelligence.forecasts import scenario_payoff, evaluate_forecasts, EVENT


def test_scenarios_include_costs_and_sensitivity_without_probability_claim():
    r = scenario_payoff(entry=100, round_trip_bps=20,
        scenarios=[{'name': 'adverse', 'exit_px': 90, 'weight': .4},
                   {'name': 'base', 'exit_px': 105, 'weight': .3},
                   {'name': 'favorable', 'exit_px': 120, 'weight': .3}])
    assert r['weighted_net_return_pct'] == pytest.approx(3.3)
    assert r['stress_net_return_pct'] == pytest.approx(1.3)
    assert not r['calibrated']


def row(i, issued, resolved, win, **kw):
    return {'episode_id': i, 'issued_at': issued, 'resolved_at': resolved, 'win': win,
            'p_win': .8, 'source': 'prospective', 'cost_complete': True,
            'playbook': 'earnings', 'model_version': '1', 'instrument_type': 'equity',
            'horizon_sessions': 1, 'forecast_event': EVENT, **kw}


def test_training_uses_resolved_time_and_cohorts_do_not_pool():
    rows = [row('train', '2026-08-01T12:00:00Z', '2026-08-02T12:00:00Z', 1),
            row('spanning', '2026-08-30T12:00:00Z', '2026-09-02T12:00:00Z', 0),
            row('test', '2026-09-03T12:00:00Z', '2026-09-04T12:00:00Z', 1),
            row('other', '2026-09-03T12:00:00Z', '2026-09-04T12:00:00Z', 0, model_version='2')]
    r = evaluate_forecasts(rows, split_at='2026-09-01T00:00:00Z')
    a = r['groups'][0]
    assert len(r['groups']) == 2 and a['n_train'] == 1 and a['n_spanning_excluded'] == 1
    assert a['baseline_brier'] == 0 and a['brier'] == pytest.approx(.04)
    assert not a['promotion_allowed']


def test_repeated_episodes_and_overlapping_windows_do_not_inflate_sample():
    r = row('x', '2026-09-03T12:00:00Z', '2026-09-09T12:00:00Z', 1)
    other = row('y', '2026-09-04T12:00:00Z', '2026-09-08T12:00:00Z', 0)
    out = evaluate_forecasts([r, r, other], split_at='2026-09-01T00:00:00Z')
    assert out['groups'][0]['n_independent'] == 1 and len(out['excluded']) == 1
