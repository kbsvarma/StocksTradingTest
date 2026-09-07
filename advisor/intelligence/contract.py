"""Versioned call contract shared by analyst and systematic discovery lanes.

Action is proposed model-book intent, never broker authorization. Existing
actionability policy remains authoritative for legacy published views.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import datetime, timezone

VERSION = 1
ACTIONS = {'enter_long', 'hold', 'reduce', 'exit', 'avoid', 'watch'}
TERMINAL = {'withdrawn', 'expired', 'resolved', 'rejected', 'superseded'}
STATES = TERMINAL | {'candidate', 'review_required', 'approved', 'conditional', 'active'}
TRANSITIONS = {
    'candidate': {'review_required', 'rejected', 'expired'},
    'review_required': {'approved', 'rejected', 'withdrawn', 'expired', 'resolved'},
    'approved': {'conditional', 'active', 'review_required', 'withdrawn', 'expired'},
    'conditional': {'active', 'review_required', 'withdrawn', 'expired', 'superseded'},
    'active': {'review_required', 'withdrawn', 'resolved', 'expired', 'superseded'},
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Timestamp must be an ISO string')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Timestamp requires timezone')
    return dt


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate(call):
    errors = []
    if call.get('schema_version') != VERSION:
        errors.append('Unsupported call schema')
    for key in ('call_id', 'ticker', 'playbook', 'model_version', 'origin'):
        if not isinstance(call.get(key), str) or not call[key].strip():
            errors.append(f'Missing {key}')
    if not re.fullmatch(r'[A-Z0-9.\-^=]{1,24}', str(call.get('ticker', ''))):
        errors.append('Invalid ticker')
    if call.get('instrument_type') not in {'equity', 'etf'}:
        errors.append('Only equity and ETF call expressions are supported')
    if call.get('action') not in ACTIONS or call.get('status') not in STATES:
        errors.append('Invalid action or state')
    if not isinstance(call.get('revision'), int) or isinstance(call.get('revision'), bool) or call['revision'] < 1:
        errors.append('Revision must be a positive integer')
    try:
        if timestamp(call['expires_at']) <= timestamp(call['issued_at']):
            errors.append('Expiry must follow issue time')
    except (ValueError, KeyError, TypeError):
        errors.append('Valid issue and expiry timestamps required')
    if call.get('recommendation_class') != 'research_idea':
        errors.append('New engine currently supports research_idea only')
    if call.get('action') == 'enter_long':
        if str(call.get('ticker','')).startswith('^') or '=' in str(call.get('ticker','')):
            errors.append('Index, FX and futures symbols cannot use an equity call expression')
        plan = call.get('plan') or {}
        fields = [plan.get(k) for k in ('stop', 'entry_low', 'entry_high', 'target')]
        if not all(number(x) and x > 0 for x in fields) or not fields[0] < fields[1] <= fields[2] < fields[3]:
            errors.append('Long plan requires 0 < stop < entry_low <= entry_high < target')
        if not isinstance(plan.get('horizon_sessions'), int) or isinstance(plan.get('horizon_sessions'), bool) or not 1 <= plan['horizon_sessions'] <= 126:
            errors.append('Horizon must be 1–126 sessions')
        if not plan.get('entry_condition') or not plan.get('invalidation'):
            errors.append('Entry condition and thesis invalidation required')
    try:
        digest(call)
    except (ValueError, TypeError):
        errors.append('Call must be finite JSON')
    return errors


def transition(call, state, *, reason, at=None):
    if state not in TRANSITIONS.get(call['status'], set()):
        raise ValueError(f'Forbidden transition {call["status"]} → {state}')
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('Transition requires a reason')
    now = at or datetime.now(timezone.utc).isoformat()
    if timestamp(now) < timestamp(call['issued_at']):
        raise ValueError('Transition predates issue')
    return {**deepcopy(call), 'status': state, 'revision': call['revision'] + 1,
            'updated_at': now, 'change_reason': reason}


def market_state(call, quote=None, *, now=None):
    """Presentation state never mutates a call or assumes an observed fill."""
    now = now or datetime.now(timezone.utc)
    if call['status'] in TERMINAL:
        return call['status']
    if now >= timestamp(call['expires_at']):
        return 'expired'
    if call['status'] in {'candidate', 'review_required'}:
        return call['status']
    if call.get('action') != 'enter_long':
        return call['status']
    quote = quote or {}
    try:
        age = (now - timestamp(quote['ts'])).total_seconds()
        fresh = 0 <= age <= 120 and quote.get('status') == 'live' and number(quote.get('px')) and quote['px'] > 0
    except (KeyError, TypeError, ValueError):
        fresh = False
    if not fresh:
        return 'quote_unavailable'
    px, plan = quote['px'], call['plan']
    if px <= plan['stop']:
        return 'invalidation_observed'
    if px >= plan['target']:
        return 'target_observed'
    if call['status'] == 'active':
        return 'active'
    return 'entry_zone' if plan['entry_low'] <= px <= plan['entry_high'] else 'waiting'


def new_call(*, ticker, episode, issued_at, expires_at, origin='underwriting', **fields):
    call = {'schema_version': VERSION, 'call_id': digest([origin, ticker, episode])[:24],
            'revision': 1, 'ticker': ticker, 'issued_at': issued_at, 'expires_at': expires_at,
            'origin': origin, 'instrument_type': 'equity', 'action': 'watch',
            'status': 'candidate', 'recommendation_class': 'research_idea',
            'playbook': 'discovery', 'model_version': '1', 'claims': [],
            'dependencies': [], 'blockers': [], 'thesis': {}, 'plan': {}, **fields}
    errors = validate(call)
    if errors:
        raise ValueError('; '.join(errors))
    return call
