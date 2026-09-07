from copy import deepcopy
import pytest
from advisor.intelligence.evidence import seal_source, review_claim, audit


def packet():
    source = seal_source({'source_id': 'release', 'url': 'https://example.com/release', 'tier': 'primary',
        'published_at': '2026-09-04T12:00:00Z', 'retrieved_at': '2026-09-04T12:01:00Z',
        'content': 'Reported diluted EPS for Q2 was USD 1.20.',
        'facts': {'eps': {'value': 1.2, 'unit': 'USD/share', 'period': '2026Q2'}}})
    claim = {'claim_id': 'eps', 'kind': 'numeric', 'source_id': 'release', 'field': 'eps',
             'value': 1.2, 'unit': 'USD/share', 'period': '2026Q2',
             'excerpt': source['content'], 'load_bearing': True}
    claim['review'] = review_claim(claim, source, author='analyst', reviewer='reviewer', at='2026-09-04T12:02:00Z')
    return claim, {'release': source}


def test_review_binds_claim_and_snapshot():
    c, s = packet()
    assert audit([c], s, as_of='2026-09-04T13:00:00Z')['ready']
    c['value'] = 1.3
    assert not audit([c], s, as_of='2026-09-04T13:00:00Z')['ready']


@pytest.mark.parametrize('field,value', [('unit', 'percent'), ('period', '2026Q3'), ('excerpt', 'Unrelated statement')])
def test_semantic_dimensions_cannot_be_swapped(field, value):
    c, s = packet(); c[field] = value
    assert not audit([c], s, as_of='2026-09-04T13:00:00Z')['ready']


def test_future_stale_tampered_and_unreviewed_evidence():
    c, s = packet()
    assert not audit([c], s, as_of='2026-09-04T11:00:00Z')['ready']
    assert not audit([c], s, as_of='2026-09-10T13:00:00Z')['ready']
    tampered = deepcopy(s); tampered['release']['content'] += ' revised'
    assert not audit([c], tampered, as_of='2026-09-04T13:00:00Z')['ready']
    c.pop('review')
    result = audit([c], s, as_of='2026-09-04T13:00:00Z')
    assert result['claims'][0]['status'] == 'source_bound' and not result['ready']


def test_empty_duplicate_and_self_review_do_not_pass():
    c, s = packet()
    assert not audit([], s, as_of='2026-09-04T13:00:00Z')['ready']
    assert not audit([c, c], s, as_of='2026-09-04T13:00:00Z')['ready']
    with pytest.raises(ValueError): review_claim(c, s['release'], reviewer='a', author='a', at='2026-09-04T13:00:00Z')


def test_missing_source_is_a_blocker_not_an_audit_crash():
    c,s=packet()
    result=audit([c],{},as_of='2026-09-04T13:00:00Z')
    assert not result['ready'] and not result['primary_present']
    assert result['claims'][0]['status']=='unsupported'
