import pytest
from advisor.intelligence.playbooks import assess, underwrite, operating_model, PLAYBOOKS, THESIS_FIELDS, UNITS
from advisor.intelligence.evidence import seal_source, review_claim


def earnings_packet():
    p = {'ticker': 'TEST', 'episode': '2026Q2', 'playbook': 'earnings_continuation',
         'event_at': '2026-09-04T12:00:00Z', 'expires_at': '2026-09-25T20:00:00Z',
         'author': 'analyst', 'claims': [], 'sources': {}, 'observations': {},
         'cost': {'round_trip_bps':20,'source':'fixture cost model','as_of':'2026-09-04T12:30:00Z'},
         'thesis': {k: f'Specific documented {k} for the test case.' for k in THESIS_FIELDS},
         'plan': {'entry_low': 98, 'entry_high': 100, 'stop': 95, 'target': 108,
                  'entry_condition': 'Post-event zone confirmation', 'invalidation': 'Guidance withdrawn'}}
    vals = [1.2, 1.0, 4.0, 4.4, 3.0, 1.0, 1.5]
    for key, value in zip(PLAYBOOKS['earnings_continuation']['required'], vals):
        time = '2026-09-04T11:00:00Z' if key == 'consensus_eps' else '2026-09-04T12:01:00Z'
        source = seal_source({'source_id': key, 'url': f'https://example.com/{key}', 'tier': 'primary',
            'published_at': time, 'retrieved_at': time, 'content': f'The reported {key} value is {value}.',
            'facts': {key: {'value': value, 'unit': UNITS[key], 'period': '2026Q2'}}})
        c = {'claim_id': key, 'kind': 'numeric', 'source_id': key, 'field': key,
             'value': value, 'unit': UNITS[key], 'period': '2026Q2', 'excerpt': source['content']}
        c['review'] = review_claim(c, source, author='analyst', reviewer='reviewer', at='2026-09-04T12:02:00Z')
        p['claims'].append(c); p['sources'][key] = source; p['observations'][key] = key
    return p


def test_complete_earnings_case_produces_reviewable_call_not_approval():
    p = earnings_packet(); c = underwrite(p, as_of='2026-09-04T13:00:00Z')
    assert c['status'] == 'review_required'
    assert c['diagnostics']['eps_surprise_pct'] == pytest.approx(20)
    assert c['recommendation_class'] == 'research_idea'


def test_post_event_consensus_cannot_impersonate_pre_event_snapshot():
    p = earnings_packet(); s = p['sources']['consensus_eps']
    s['retrieved_at'] = '2026-09-04T12:01:00Z'
    assert 'Consensus must be captured before the earnings event' in assess(p, as_of='2026-09-04T13:00:00Z')['blockers']


def test_incomplete_packet_stays_candidate_with_exact_missing_inputs():
    p = earnings_packet(); p['claims'] = []; p['thesis'].pop('why_not_priced')
    c = underwrite(p, as_of='2026-09-04T13:00:00Z')
    assert c['status'] == 'candidate' and any('actual_eps' in x for x in c['blockers'])


def test_operating_model_is_reproducible_assumption_sensitivity():
    r = operating_model(revenue=1000, operating_margin=.2, interest=10, tax_rate=.25, diluted_shares=100, multiple=20)
    assert r['eps'] == 1.425 and r['scenario_price'] == 28.5
    with pytest.raises(ValueError): operating_model(revenue=1000, operating_margin=.2, interest=10, tax_rate=.25, diluted_shares=0, multiple=20)
