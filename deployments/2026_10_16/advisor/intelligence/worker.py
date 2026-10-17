"""Offline intelligence worker: ingest packets, reassess calls, publish health.

Run: python -m advisor.intelligence.worker --data-dir PATH
Inbox: PATH/intelligence/inbox/*.json (complete playbook packets).
The worker never approves its own calls, fetches URLs, sends messages or trades.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from advisor.intelligence.access import Principal
from advisor.intelligence.store import CallStore
from advisor.intelligence.playbooks import underwrite
from advisor.intelligence.contract import digest, timestamp
from advisor.intelligence.events import process
from advisor.intelligence.adapters import read_json, read_rows, snapshot
from advisor.research.suggestion_store import atomic_json


def run(data, *, tenant='model', now=None):
    now = now or datetime.now(timezone.utc)
    data = Path(data); root = data/'intelligence'
    errors, ingested, changed = [], 0, []
    with CallStore(root/'calls.sqlite', Principal('intelligence-worker', tenant, 'service')) as store:
        for path in sorted((root/'inbox').glob('*.json')):
            try:
                packet = json.loads(path.read_text())
                decision=timestamp(packet.get('decision_at') or now.isoformat())
                if decision>now:
                    raise ValueError('Decision time is in the future')
                # Old already-ingested packets remain valid history; do not
                # issue a new backdated call into the prospective record.
                call = underwrite(packet, as_of=packet.get('decision_at') or now.isoformat())
                old = store.latest(call['call_id'])
                if old and old.get('packet_hash') == call['packet_hash']: continue
                if (now-decision).total_seconds()>72*3600:
                    raise ValueError('Backdated packet cannot enter prospective call history')
                if old:
                    # A revision keeps episode issue time and never inherits an approval.
                    call.update(revision=old['revision']+1, issued_at=old['issued_at'],
                                status='review_required', updated_at=now.isoformat(),
                                change_reason='Underwriting packet revised',
                                entry_observed_px=old.get('entry_observed_px'),
                                entry_observed_at=old.get('entry_observed_at'))
                store.put(call, expected_revision=old['revision'] if old else 0)
                ingested += 1
            except Exception as exc:
                errors.append({'file': path.name, 'error': f'{type(exc).__name__}: {exc}'})
        for row in read_rows(data/'alerts'/'intraday_alerts.jsonl'):
            try:
                event = {'event_id': digest(row), 'ticker': row.get('ticker'), 'kind': 'filing',
                         'source': row.get('src') or 'filing-poller', 'observed_at': row['ts'],
                         'summary': f'{row.get("form", "Filing")} published', 'url': row.get('url')}
                changed.extend(process(store, event, now=now))
            except Exception as exc: errors.append({'event': row.get('ticker'), 'error': str(exc)})
        snap = snapshot(data, principal=Principal('intelligence-worker', tenant, 'service'), now=now)
        for ticker, q in snap['quotes'].items():
            if q.get('status') != 'live': continue
            event = {'event_id': digest([ticker, q['ts'], q.get('px')]), 'ticker': ticker, 'kind': 'quote',
                     'source': q.get('src') or 'quote-daemon', 'observed_at': q['ts'],
                     'px': q.get('px'), 'quote_status': 'live'}
            try: changed.extend(process(store, event, now=now))
            except Exception as exc: errors.append({'quote': ticker, 'error': str(exc)})
        clock_at = now.replace(second=0, microsecond=0).isoformat()
        changed.extend(process(store, {'event_id': 'clock:'+clock_at, 'kind': 'clock',
                                      'source': 'exchange-clock', 'observed_at': clock_at}, now=now))
        queue=[{'call_id':c['call_id'],'ticker':c['ticker'],'playbook':c['playbook'],
                'author':c.get('author'),'thesis':c.get('thesis'), 'blockers':c.get('blockers'),
                'packet_hash':c.get('packet_hash'),'issued_at':c['issued_at'],
                'publication_lineage':c.get('publication_lineage',{})}
               for c in store.latest() if c['status'] in {'candidate','review_required'}]
        atomic_json(root/'research_queue.json',{'as_of':now.isoformat(),'calls':queue[:100]})
    status = {'schema_version': 1, 'as_of': now.isoformat(), 'status': 'degraded' if errors else 'healthy',
              'packets_ingested': ingested, 'changes': changed, 'errors': errors,
              'calls_available': len(snap['calls']), 'new_call_approval': 'independent reviewer required'}
    atomic_json(root/'worker_status.json', status)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('ADVISOR_DATA_DIR', Path(__file__).resolve().parents[1]/'data')))
    parser.add_argument('--tenant', default='model')
    args = parser.parse_args()
    result = run(args.data_dir, tenant=args.tenant)
    print(json.dumps(result, indent=2))
    return 1 if result['errors'] else 0


if __name__ == '__main__': raise SystemExit(main())
