from datetime import datetime, timezone
import pytest
from advisor.intelligence.contract import new_call, transition, market_state, validate


def call(**kw):
    fields = dict(ticker='TEST', episode='event-1', issued_at='2026-09-04T12:00:00Z',
                  expires_at='2026-09-25T20:00:00Z', action='enter_long',
                  plan={'entry_low': 98, 'entry_high': 100, 'stop': 95, 'target': 108,
                        'horizon_sessions': 15, 'entry_condition': 'observed zone',
                        'invalidation': 'guidance withdrawn'})
    fields.update(kw)
    return new_call(**fields)


def test_identity_is_episode_stable_and_origin_separated():
    assert call()['call_id'] == call()['call_id']
    assert call()['call_id'] != call(origin='suggestion')['call_id']


@pytest.mark.parametrize('change', [{'instrument_type': 'option'}, {'recommendation_class': 'actionable_idea'},
                                   {'revision': True}, {'expires_at': '2026-09-04'}, {'plan': {}}])
def test_unsupported_or_malformed_contract_fails(change):
    with pytest.raises(ValueError): call(**change)


def test_candidate_cannot_skip_review():
    with pytest.raises(ValueError): transition(call(), 'active', reason='jump')
    reviewed = transition(call(), 'review_required', reason='packet complete')
    assert reviewed['revision'] == 2
    assert call()['revision'] == 1


def test_quote_observation_is_not_activation_and_stop_precedes_entry():
    c = call(status='conditional')
    now = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
    q = {'px': 99, 'ts': now.isoformat(), 'status': 'live'}
    assert market_state(c, q, now=now) == 'entry_zone'
    assert c['status'] == 'conditional'
    assert market_state(c, {**q, 'px': 94}, now=now) == 'invalidation_observed'
    assert market_state(c, {**q, 'status': 'delayed'}, now=now) == 'quote_unavailable'
    assert market_state(c, q, now=datetime(2026, 10, 1, tzinfo=timezone.utc)) == 'expired'


def test_no_nan_in_contract():
    assert validate({**call(), 'score': float('nan')})
