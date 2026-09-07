import sqlite3
import pytest
from advisor.intelligence.store import CallStore
from advisor.intelligence.access import Principal, resolve_identity
from advisor.intelligence.contract import transition
from advisor.tests.test_intelligence_contract import call


def test_transactional_revisions_tenant_isolation_and_backup(tmp_path):
    path = tmp_path/'calls.sqlite'
    with CallStore(path, Principal('author', 'a', 'admin')) as store:
        c = call(); store.put(c)
        updated = transition(c, 'review_required', reason='evidence ready')
        store.put(updated, expected_revision=1)
        with pytest.raises(ValueError, match='conflict'): store.put(updated, expected_revision=1)
        assert len(store.history(c['call_id'])) == 2
        export = store.export(); assert len(export['audit']) == 2
        store.backup(tmp_path/'backup.sqlite')
        with pytest.raises(sqlite3.IntegrityError):
            store.db.execute('UPDATE revisions SET hash="tamper"')
    with CallStore(path, Principal('other', 'b', 'viewer'), readonly=True) as other:
        assert other.latest() == [] and other.history(c['call_id']) == []
        with pytest.raises(PermissionError): other.put(call())
    with CallStore(tmp_path/'backup.sqlite', Principal('author', 'a', 'admin'), readonly=True) as restored:
        assert restored.export()['checkpoint'] == export['checkpoint']


def test_self_approval_and_missing_evidence_are_blocked(tmp_path):
    path = tmp_path/'calls.sqlite'
    c = call(status='review_required', author='author')
    with CallStore(path, Principal('author', 'a', 'admin')) as store:
        store.put(c)
        with pytest.raises(PermissionError): store.review(c['call_id'], expected_revision=1, verdict='approve', reason='yes')
    with CallStore(path, Principal('reviewer', 'a', 'reviewer')) as store:
        with pytest.raises(ValueError, match='evidence'): store.review(c['call_id'], expected_revision=1, verdict='approve', reason='yes')
        r = store.review(c['call_id'], expected_revision=1, verdict='reject', reason='unsupported')
        assert r['status'] == 'rejected'


def test_identity_requires_verified_provisioned_email():
    p = {'members': {'user@example.com': {'tenant': 'fund-a', 'role': 'reviewer'}}}
    assert resolve_identity({'is_logged_in': True, 'email_verified': True, 'email': 'user@example.com'}, p).tenant == 'fund-a'
    with pytest.raises(PermissionError): resolve_identity({'is_logged_in': True, 'email_verified': False, 'email': 'user@example.com'}, p)
    with pytest.raises(PermissionError): resolve_identity({'is_logged_in': True, 'email_verified': True, 'email': 'unknown@example.com'}, p)
