from datetime import datetime, timezone
import pytest
from advisor.intelligence.store import CallStore
from advisor.intelligence.access import Principal
from advisor.intelligence.events import process
from advisor.intelligence.performance import scorecard, portfolio_impact, observed_outcome
from advisor.tests.test_intelligence_playbooks import earnings_packet
from advisor.intelligence.playbooks import underwrite

NOW = datetime(2026, 9, 4, 14, tzinfo=timezone.utc)


def approved_store(tmp_path):
    path = tmp_path/'calls.sqlite'
    c = underwrite(earnings_packet(), as_of='2026-09-04T13:00:00Z')
    c['plan']['trigger_kind'] = 'price_zone'
    with CallStore(path, Principal('analyst', 'a', 'analyst')) as s: s.put(c)
    with CallStore(path, Principal('reviewer', 'a', 'reviewer')) as s:
        s.review(c['call_id'], expected_revision=1, verdict='approve', reason='Reviewed complete packet', at='2026-09-04T13:30:00Z')
    return path, c


def test_material_event_reopens_review_once_and_reused_id_fails(tmp_path):
    path, c = approved_store(tmp_path)
    event = {'event_id': 'guidance-1', 'ticker': 'TEST', 'kind': 'guidance', 'source': 'issuer',
             'observed_at': NOW.isoformat(), 'summary': 'Guidance changed'}
    with CallStore(path, Principal('service', 'a', 'service')) as s:
        assert process(s, event, now=NOW)[0]['state'] == 'review_required'
        assert process(s, event, now=NOW) == []
        assert s.latest(c['call_id'])['blockers']
        with pytest.raises(ValueError): process(s, {**event, 'summary': 'different'}, now=NOW)


def test_live_entry_then_gap_exit_is_observed_not_assumed_exact_stop(tmp_path):
    path, c = approved_store(tmp_path)
    q = {'event_id': 'q1', 'ticker': 'TEST', 'kind': 'quote', 'source': 'test-feed',
         'observed_at': NOW.isoformat(), 'px': 99, 'quote_status': 'live'}
    with CallStore(path, Principal('service', 'a', 'service')) as s:
        assert process(s, {**q, 'quote_status': 'delayed'}, now=NOW) == []
        assert process(s, {**q, 'event_id': 'q2'}, now=NOW)[0]['state'] == 'active'
        assert process(s, {**q, 'event_id': 'q3', 'px': 92}, now=NOW)[0]['state'] == 'resolved'
        outcome = observed_outcome(s.latest(c['call_id']))
        assert outcome['net_return_pct'] == pytest.approx((92/99-1)*100-.2)


def test_scorecard_excludes_replays_and_nonmatched_benchmarks():
    row = {'episode_id': 'x', 'issued_at': '2026-09-04T12:00:00Z', 'source': 'prospective',
           'activated': True, 'status': 'resolved', 'cost_complete': True, 'net_return_pct': 2,
           'benchmark_return_pct': 1, 'benchmark_window': 'same_activated_window'}
    r = scorecard([row, row, {**row, 'episode_id': 'y', 'source': 'replay'},
                   {**row, 'episode_id': 'z', 'benchmark_window': 'different'}])
    assert r['coverage']['duplicates_or_missing_identity'] == 1
    assert r['groups'][0]['n_resolved'] == 2 and r['groups'][0]['n_benchmark_pairs'] == 1


def test_portfolio_missing_context_and_real_concentration():
    c = {'ticker': 'TEST', 'sector': 'Tech', 'allocation': {'weight_pct': 5}, 'drivers': ['rates']}
    assert not portfolio_impact(c, {}, now=NOW)['sizing_available']
    p = {'as_of': NOW.isoformat(), 'status': 'verified',
         'holdings': [{'ticker': 'TEST', 'sector': 'Tech', 'weight_pct': 8, 'drivers': ['rates']}],
         'mandate': {'max_name_pct': 10, 'max_sector_pct': 30, 'max_gross_pct': 100}}
    r = portfolio_impact(c, p, now=NOW)
    assert r['name_after_pct'] == 13 and r['shared_drivers'] == ['rates'] and r['blockers']
