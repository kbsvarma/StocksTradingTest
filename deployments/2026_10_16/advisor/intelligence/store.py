"""Transactional append-only call history, optimistic concurrency and audit chain.

SQLite is the single-host deployment store. Tenant isolation is enforced at every
application query, not advertised as database row-level security. Audit hashes
detect corruption; external export/checkpoint retention is needed against a host
administrator rewriting both history and hashes.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from advisor.intelligence.contract import validate, digest, transition, TRANSITIONS
from advisor.intelligence.access import Principal

SCHEMA = '''
CREATE TABLE IF NOT EXISTS revisions (
 tenant TEXT NOT NULL, call_id TEXT NOT NULL, revision INTEGER NOT NULL,
 payload TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY(tenant, call_id, revision));
CREATE TABLE IF NOT EXISTS audit (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, tenant TEXT NOT NULL, actor TEXT NOT NULL,
 at TEXT NOT NULL, kind TEXT NOT NULL, object_id TEXT NOT NULL,
 payload TEXT NOT NULL, previous_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
 tenant TEXT NOT NULL, event_id TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(tenant, event_id));
CREATE TABLE IF NOT EXISTS event_receipts (
 tenant TEXT NOT NULL, event_id TEXT NOT NULL, call_id TEXT NOT NULL,
 PRIMARY KEY(tenant, event_id, call_id));
CREATE TABLE IF NOT EXISTS workspaces (
 tenant TEXT NOT NULL, actor TEXT NOT NULL, name TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(tenant, actor, name));
CREATE TRIGGER IF NOT EXISTS immutable_revisions_update BEFORE UPDATE ON revisions BEGIN SELECT RAISE(ABORT, 'immutable revision'); END;
CREATE TRIGGER IF NOT EXISTS immutable_revisions_delete BEFORE DELETE ON revisions BEGIN SELECT RAISE(ABORT, 'immutable revision'); END;
CREATE TRIGGER IF NOT EXISTS immutable_audit_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'immutable audit'); END;
CREATE TRIGGER IF NOT EXISTS immutable_audit_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'immutable audit'); END;
'''


class CallStore:
    def __init__(self, path, principal: Principal, *, readonly=False):
        self.path, self.principal, self.readonly = Path(path), principal, readonly
        principal.require('read')
        if readonly:
            self.db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path, timeout=10)
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.executescript(SCHEMA)
        self.db.row_factory = sqlite3.Row

    def __enter__(self): return self
    def __exit__(self, *args): self.db.close()

    def latest(self, call_id=None):
        self.principal.require('read')
        sql = '''SELECT payload, hash FROM revisions r WHERE tenant=? AND revision=
                 (SELECT MAX(revision) FROM revisions WHERE tenant=r.tenant AND call_id=r.call_id)'''
        params = [self.principal.tenant]
        if call_id is not None:
            sql += ' AND call_id=?'; params.append(call_id)
        rows = []
        for r in self.db.execute(sql, params):
            call = json.loads(r['payload'])
            if digest(call) != r['hash']: raise ValueError('Call history integrity failure')
            rows.append(call)
        return (rows[0] if rows else None) if call_id is not None else rows

    def history(self, call_id):
        self.principal.require('read')
        rows=[]
        for row in self.db.execute('SELECT payload,hash FROM revisions WHERE tenant=? AND call_id=? ORDER BY revision',
                                  (self.principal.tenant, call_id)):
            call=json.loads(row['payload'])
            if digest(call)!=row['hash']:raise ValueError('Revision integrity failure')
            rows.append(call)
        return rows

    def _audit(self, kind, object_id, payload):
        previous = self.db.execute('SELECT hash FROM audit WHERE tenant=? ORDER BY seq DESC LIMIT 1', (self.principal.tenant,)).fetchone()
        row = {'tenant': self.principal.tenant, 'actor': self.principal.user,
               'at': datetime.now(timezone.utc).isoformat(), 'kind': kind, 'object_id': object_id,
               'payload': payload, 'previous_hash': previous['hash'] if previous else ''}
        self.db.execute('INSERT INTO audit(tenant,actor,at,kind,object_id,payload,previous_hash,hash) VALUES (?,?,?,?,?,?,?,?)',
            (row['tenant'], row['actor'], row['at'], kind, object_id, json.dumps(payload, sort_keys=True, allow_nan=False), row['previous_hash'], digest(row)))

    def _insert(self, call, expected_revision, *, reviewing=False, operating=False):
        errors = validate(call)
        if errors: raise ValueError('; '.join(errors))
        existing = self.latest(call['call_id'])
        actual = existing['revision'] if existing else 0
        if actual != expected_revision: raise ValueError('Revision conflict; reload before editing')
        if call['revision'] != actual + 1: raise ValueError('Revision must increment exactly once')
        if existing:
            for key in ('ticker', 'origin', 'issued_at', 'call_id', 'submitted_by'):
                if call.get(key) != existing.get(key): raise ValueError(f'Cannot rewrite episode {key}')
            if call['status'] != existing['status'] and call['status'] not in TRANSITIONS.get(existing['status'], set()):
                raise ValueError('Forbidden lifecycle transition')
            if existing['status'] in {'withdrawn', 'expired', 'resolved', 'rejected', 'superseded'}:
                raise ValueError('Closed episode cannot be rewritten')
            if not reviewing and call['status'] == 'approved':
                raise PermissionError('Approval requires independent review')
            if not reviewing and not operating and existing['status'] in {'approved', 'conditional', 'active'} and call['status'] != 'review_required':
                raise PermissionError('Editing a published plan requires renewed review')
        elif call['status'] not in {'candidate', 'review_required'}:
            raise PermissionError('New calls must enter through review')
        self.db.execute('INSERT INTO revisions VALUES (?,?,?,?,?)',
            (self.principal.tenant, call['call_id'], call['revision'], json.dumps(call, sort_keys=True, allow_nan=False), digest(call)))
        self._audit('call_revision', call['call_id'], {'revision': call['revision'], 'call_hash': digest(call), 'state': call['status']})

    def put(self, call, *, expected_revision=0):
        self.principal.require('propose')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            existing=self.latest(call['call_id'])
            call={**call, 'submitted_by': existing.get('submitted_by') if existing else self.principal.user,
                  'last_editor':self.principal.user}
            self._insert(call, expected_revision)
        return call

    def review(self, call_id, *, expected_revision, verdict, reason, at=None):
        self.principal.require('review')
        if verdict not in {'approve', 'reject'}: raise ValueError('Unknown review verdict')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            call = self.latest(call_id)
            if not call or call['status'] != 'review_required': raise ValueError('Call is not awaiting review')
            if self.principal.user in {call.get('author'),call.get('submitted_by'),call.get('last_editor')}: raise PermissionError('Author, submitter or last editor cannot approve own call')
            if verdict == 'approve':
                if call.get('blockers'): raise ValueError('Unresolved underwriting blockers')
                from advisor.intelligence.evidence import audit
                check = audit(call.get('claims') or [], call.get('source_snapshot') or {},
                              as_of=at or datetime.now(timezone.utc).isoformat(), author=call.get('author'))
                if not check['ready'] or not check['primary_present']: raise ValueError('Current verified evidence required')
                from advisor.intelligence.playbooks import assess
                checked=assess({**call,'sources':call.get('source_snapshot') or {}},
                               as_of=at or datetime.now(timezone.utc).isoformat())
                if not checked['ready_for_review']:
                    raise ValueError('Current underwriting failed: '+'; '.join(checked['blockers']))
            updated = transition(call, 'approved' if verdict == 'approve' else 'rejected', reason=reason, at=at)
            updated['review'] = {'reviewer': self.principal.user, 'verdict': verdict, 'reason': reason,
                                 'reviewed_hash': digest(call), 'at': updated['updated_at']}
            self._insert(updated, expected_revision, reviewing=True)
        return updated

    def export(self):
        self.principal.require('export')
        revisions = [json.loads(r['payload']) for r in self.db.execute('SELECT payload FROM revisions WHERE tenant=? ORDER BY call_id,revision', (self.principal.tenant,))]
        audit = []
        previous = ''
        for r in self.db.execute('SELECT * FROM audit WHERE tenant=? ORDER BY seq', (self.principal.tenant,)):
            row = {k: r[k] for k in ('tenant', 'actor', 'at', 'kind', 'object_id', 'previous_hash')}
            row['payload'] = json.loads(r['payload'])
            if row['previous_hash'] != previous or digest(row) != r['hash']:
                raise ValueError('Audit chain integrity failure')
            previous = r['hash']; audit.append({**row, 'hash': r['hash']})
        by_revision = {(c['call_id'], c['revision']): digest(c) for c in revisions}
        for row in audit:
            if row['kind'] == 'call_revision' and by_revision.get((row['object_id'], row['payload']['revision'])) != row['payload']['call_hash']:
                raise ValueError('Revision does not match audit checkpoint')
        return {'schema_version': 1, 'tenant': self.principal.tenant, 'calls': revisions,
                'audit': audit, 'checkpoint': previous,
                'note': 'Retain this checkpoint independently to detect whole-store replacement'}

    def backup(self, destination):
        self.principal.require('operate')
        destination = Path(destination)
        if destination.exists(): raise ValueError('Backup destination already exists')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(destination) as target:
            self.db.backup(target)
        return destination

    def workspace(self, name='default'):
        self.principal.require('read')
        try:
            row=self.db.execute('SELECT payload FROM workspaces WHERE tenant=? AND actor=? AND name=?',
                                (self.principal.tenant,self.principal.user,name)).fetchone()
        except sqlite3.OperationalError:
            return {}  # older read-only stores are migrated by the worker
        return json.loads(row['payload']) if row else {}

    def save_workspace(self, payload, name='default'):
        self.principal.require('propose')
        if not isinstance(payload,dict) or len(json.dumps(payload))>50000 or not isinstance(name,str) or len(name)>80:
            raise ValueError('Invalid workspace payload')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('INSERT INTO workspaces VALUES (?,?,?,?) ON CONFLICT(tenant,actor,name) DO UPDATE SET payload=excluded.payload',
                (self.principal.tenant,self.principal.user,name,json.dumps(payload,sort_keys=True,allow_nan=False)))
            self._audit('workspace_saved',name,{'hash':digest(payload)})
