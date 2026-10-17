"""Idempotent event-to-thesis routing in the call store transaction."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from advisor.intelligence.contract import timestamp, digest, transition, market_state, TERMINAL

MATERIAL = {'earnings', 'guidance', 'estimate_revision', 'filing', 'macro'}


def process(store, event, *, now=None):
    store.principal.require('operate')
    now = now or datetime.now(timezone.utc)
    if event.get('kind') not in MATERIAL | {'quote', 'clock'}:
        raise ValueError('Unsupported event kind')
    if not isinstance(event.get('event_id'), str) or not event['event_id'] or not event.get('source'):
        raise ValueError('Source and immutable event ID required')
    observed = timestamp(event['observed_at'])
    if observed > now: raise ValueError('Future event')
    tenant, affected = store.principal.tenant, []
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        existing = store.db.execute('SELECT payload FROM events WHERE tenant=? AND event_id=?', (tenant, event['event_id'])).fetchone()
        encoded = json.dumps(event, sort_keys=True, allow_nan=False)
        if existing and existing['payload'] != encoded:
            raise ValueError('Event ID reused for different content')
        store.db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?)', (tenant, event['event_id'], encoded))
        for call in store.latest():
            if call['status'] in TERMINAL or (event['kind'] != 'clock' and observed < timestamp(call['issued_at'])): continue
            if event['kind'] != 'clock' and event.get('ticker') != call['ticker'] and not any(
                d.get('ticker') == event.get('ticker') and d.get('kind') == event['kind'] for d in call.get('dependencies', [])):
                continue
            if store.db.execute('SELECT 1 FROM event_receipts WHERE tenant=? AND event_id=? AND call_id=?', (tenant, event['event_id'], call['call_id'])).fetchone():
                continue
            state, reason = None, None
            metadata = {}
            if now >= timestamp(call['expires_at']):
                state, reason = 'expired', 'Call horizon elapsed; no extension inferred'
            elif event['kind'] in MATERIAL and any(d.get('kind') == event['kind'] for d in call.get('dependencies', [])):
                if call['status'] not in {'candidate', 'review_required'}:
                    state = 'review_required'
                reason = f'{event["kind"].replace("_", " ").title()} affects a load-bearing dependency: {event.get("summary", "new source available")}'
                metadata = {'review_event': event, 'pre_review_state': call.get('pre_review_state', call['status'])}
            elif event['kind'] == 'quote':
                quote = {'px': event.get('px'), 'ts': event['observed_at'], 'status': event.get('quote_status')}
                # A review hold blocks new entry, but must never hide a hard
                # risk crossing on an already activated model-book episode.
                observation_call = {**call, 'status': 'active'} if call.get('entry_observed_px') and call['status'] == 'review_required' else call
                market = market_state(observation_call, quote, now=now)
                if market == 'entry_zone' and call['status'] in {'approved', 'conditional'}:
                    # Additional trigger confirmation must be explicitly present.
                    if call['plan'].get('trigger_kind', 'reviewed_confirmation') == 'price_zone' or event.get('confirmation_id') == call['plan'].get('confirmation_id') and event.get('confirmation_id'):
                        state, reason = 'active', 'Model-book entry condition observed; not a broker fill'
                        metadata = {'entry_observed_px': event['px'], 'entry_observed_at': event['observed_at'],
                                    'entry_basis': 'live_quote_observation_not_fill'}
                elif market in {'invalidation_observed', 'target_observed'}:
                    if call.get('entry_observed_px') and call['status'] in {'active', 'review_required'}:
                        state, reason = 'resolved', market.replace('_', ' ')
                        metadata = {'exit_observed_px': event['px'], 'exit_observed_at': event['observed_at'],
                                    'outcome_basis': 'quote_observation_not_fill'}
                    elif call['status'] in {'approved', 'conditional'}:
                        state, reason = 'withdrawn', f'{market.replace("_", " ")} before activation'
            if state:
                updated = transition(call, state, reason=reason, at=now.isoformat())
                updated.update(metadata)
                if state == 'review_required': updated['blockers'] = list(dict.fromkeys(call.get('blockers', []) + ['Material event requires renewed underwriting']))
                store._insert(updated, call['revision'], operating=True)
                affected.append({'call_id': call['call_id'], 'ticker': call['ticker'], 'state': state, 'reason': reason})
            elif reason:
                # A second event while already under review remains visible in
                # audit; no need to manufacture another identical call revision.
                affected.append({'call_id': call['call_id'], 'ticker': call['ticker'], 'state': call['status'], 'reason': reason})
            store.db.execute('INSERT INTO event_receipts VALUES (?,?,?)', (tenant, event['event_id'], call['call_id']))
        if affected:
            store._audit('event_reassessment', event['event_id'], {'event_hash': digest(event), 'affected': affected,
                         'latency_seconds': (now - observed).total_seconds()})
    return affected
