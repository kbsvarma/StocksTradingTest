"""Read-only projection of both existing research lanes into the command desk.

Historical projections cannot promote calls. Corrupt or missing files produce
visible diagnostics; no network request or operational write occurs on render.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
from advisor.intelligence.contract import new_call, timestamp, digest, market_state
from advisor.intelligence.access import Principal
from advisor.intelligence.store import CallStore
from advisor.intelligence.performance import scorecard, observed_outcome


def read_json(path, issues=None):
    try:
        d = json.loads(Path(path).read_text())
        if not isinstance(d, dict): raise ValueError('Expected object')
        return d
    except FileNotFoundError: return {}
    except (ValueError, OSError) as exc:
        if issues is not None: issues.append(f'{Path(path).name}: {type(exc).__name__}')
        return {}


def read_rows(path, issues=None):
    try: lines = Path(path).read_text().splitlines()
    except FileNotFoundError: return []
    except OSError:
        if issues is not None: issues.append(f'{Path(path).name}: unreadable')
        return []
    rows = []
    for i, line in enumerate(lines):
        if not line.strip(): continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict): raise ValueError('Expected object')
            rows.append(row)
        except ValueError:
            if issues is not None: issues.append(f'{Path(path).name}: corrupt row {i+1}')
    return rows


def normalize_time(value, fallback=None):
    try:
        if len(str(value)) == 10: value += 'T20:00:00+00:00'
        return timestamp(value).isoformat()
    except (ValueError, TypeError): return fallback


def tracked_symbols(data, *, tenant='model'):
    """Shared subscription inventory for filing and quote providers."""
    path=Path(data)/'intelligence'/'calls.sqlite'
    if not path.exists():return []
    with CallStore(path,Principal('subscription-reader',tenant,'service'),readonly=True) as store:
        return sorted({c['ticker'] for c in store.latest()
                       if c['status'] in {'approved','conditional','active','review_required'}
                       and datetime.now(timezone.utc)<timestamp(c['expires_at'])})


def project_view(view, *, now):
    issued = normalize_time(view.get('ts') or view.get('data_as_of'))
    if not issued: raise ValueError('View issue time unavailable')
    expiry = normalize_time(view.get('time_stop')) or (timestamp(issued) + timedelta(days=30)).isoformat()
    if timestamp(expiry) <= timestamp(issued): expiry = (timestamp(issued) + timedelta(seconds=1)).isoformat()
    original = view.get('status', 'open')
    status = 'resolved' if original in {'closed', 'hit_target', 'stopped', 'time_stop', 'resolved'} else 'withdrawn' if original == 'withdrawn' else 'expired' if now >= timestamp(expiry) else 'review_required'
    plan = {k: view.get(old) for k, old in (('entry_low', 'entry_px_low'), ('entry_high', 'entry_px_high'), ('stop', 'stop_px'), ('target', 'target_px'))}
    thesis = {'what_changed': view.get('thesis') or '',
              'catalyst': (view.get('catalyst') or {}).get('event', '') if isinstance(view.get('catalyst'), dict) else view.get('catalyst', ''),
              'invalidation': view.get('watch', ''), 'contrary_evidence': '; '.join(view.get('disconfirmers') or [])}
    return new_call(ticker=view.get('yf_ticker') or view.get('instrument'), episode=view.get('id') or view.get('decision_key') or issued,
        issued_at=issued, expires_at=expiry, origin='brief', status=status,
        action='watch' if view.get('direction') != 'short' else 'avoid',
        playbook=view.get('source') or 'analyst_research', plan=plan, thesis=thesis,
        direction=view.get('direction', 'long'), legacy_id=view.get('id'),
        legacy_class=view.get('recommendation_class'), legacy_view=view,
        claims=view.get('evidence') or [],
        blockers=['Historical brief projection; new-contract underwriting required'] if status == 'review_required' else [],
        sector=view.get('sector', 'Unclassified'))


def snapshot(data, *, principal=None, now=None):
    data, now = Path(data), now or datetime.now(timezone.utc)
    principal = principal or Principal('local-viewer', 'model', 'viewer')
    principal.require('read')
    issues = []
    # A tenant must have an explicit data root; callers must not expose the
    # shared operator journal as a fallback for customer tenants.
    research = data/'research'
    slate = read_json(research/'candidates_latest.json', issues)
    candidates=[]
    for row in slate.get('slate', []) if isinstance(slate.get('slate'),list) else []:
        if not isinstance(row,dict) or not re.fullmatch(r'[A-Z0-9.\-^=]{1,24}',str(row.get('ticker',''))) or not isinstance(row.get('detail',{}),dict):
            issues.append('Malformed candidate omitted from security navigation');continue
        candidates.append(row)
    slate['slate']=candidates
    picks = read_json(research/'picks_latest.json', issues)
    quotes = read_json(data/'quotes'/'latest.json', issues)
    signals = read_json(research/'signals_latest.json', issues)
    calls, saved_ids = [], set()
    database = data/'intelligence'/'calls.sqlite'
    if database.exists():
        try:
            with CallStore(database, principal, readonly=True) as store:
                calls = store.latest()
                saved_ids = {c.get('legacy_id') for c in calls}
        except Exception as exc:
            issues.append(f'Call store unavailable: {type(exc).__name__}')
    effective, origins, original_rows = {}, {}, {}
    for row in read_rows(data/'decision_journal.jsonl', issues):
        key = row.get('id')
        if not key: continue
        origins.setdefault(key, row.get('type'))
        original_rows.setdefault(key, row)
        effective[key] = {**effective.get(key, {}), **row}
    for key, view in effective.items():
        if origins[key] != 'view' or key in saved_ids: continue
        # Preserve the original issue timestamp rather than resolution time.
        origin = original_rows[key]
        try: calls.append(project_view({**view, 'ts': origin.get('ts')}, now=now))
        except (ValueError, TypeError): issues.append(f'Historical view {key}: unsupported contract')
    # A suggestion projection retains immutable ID and evidence; no approval
    # status is imported merely because a name ranked highly.
    for pick in picks.get('picks') or []:
        try:
            issued = normalize_time(picks.get('as_of'))
            expiry = normalize_time(pick.get('expires_on')) or (timestamp(issued) + timedelta(days=30)).isoformat()
            if timestamp(expiry) <= timestamp(issued): continue
            c = new_call(ticker=pick['ticker'], episode=pick.get('episode_id') or pick.get('id') or issued,
                issued_at=issued, expires_at=expiry, origin='suggestion',
                status='expired' if now >= timestamp(expiry) else 'candidate',
                action='avoid' if pick.get('direction') == 'short' else 'watch',
                playbook=(pick.get('selection') or {}).get('lead_bucket') or 'discovery',
                plan={k: pick.get(k) for k in ('entry_low', 'entry_high', 'stop', 'target')},
                thesis=(pick.get('evidence') or {}).get('thesis') or {},
                score=pick.get('score'), blockers=(pick.get('triage') or {}).get('blockers') or ['Independent call review required'],
                lineage={'release_id': picks.get('release_id'), 'suggestion_id': pick.get('id')},
                sector=pick.get('sector', 'Unclassified'))
            if not any(x['call_id'] == c['call_id'] for x in calls): calls.append(c)
        except (KeyError, ValueError, TypeError): issues.append('Suggestion could not be projected')
    normalized_quotes = {}
    for ticker, q in (quotes.get('quotes') or {}).items():
        try:
            from advisor.suggestion_policy import quote_is_fresh, exchange_session_open
            live = quote_is_fresh(q, now) and exchange_session_open(now) and q.get('kind') != 'close' and 'delayed' not in str(q.get('src', '')).lower()
        except Exception: live = False
        normalized_quotes[ticker] = {**q, 'ts': q.get('market_ts') or q.get('ts'), 'status': 'live' if live else 'reference'}
    for call in calls:
        call['market_state'] = market_state(call, normalized_quotes.get(call['ticker']), now=now)
    events = read_rows(data/'alerts'/'intraday_alerts.jsonl', issues)[-100:]
    packets = read_json(data/'intelligence'/'worker_status.json', issues)
    outcomes = read_json(data/'intelligence'/'outcomes.json', issues).get('rows', [])
    for c in calls:
        if c.get('entry_observed_px'):
            o = observed_outcome(c)
            outcomes.append({**o, 'episode_id': c['call_id'], 'issued_at': c['issued_at'],
                'activated': True, 'playbook': c['playbook'], 'model_version': c['model_version'],
                'source': 'prospective', 'basis': o.get('basis', 'quote_observation')})
    freshness = {'status': 'missing', 'as_of': slate.get('as_of'), 'reason': 'Candidate release unavailable'}
    try:
        from advisor.suggestion_policy import last_complete_session
        required = last_complete_session(now)
        current = timestamp(slate['as_of']).date().isoformat() >= required and timestamp(slate['as_of']) <= now
        freshness = {'status': 'current' if current else 'stale', 'as_of': slate['as_of'],
                     'reason': f'Required completed session: {required}'}
    except (ValueError, KeyError, TypeError): pass
    return {'schema_version': 1, 'as_of': now.isoformat(), 'tenant': principal.tenant,
            'calls': sorted(calls, key=lambda c: c['issued_at'], reverse=True),
            'candidates': slate.get('slate') or [], 'candidate_meta': {k:v for k,v in slate.items() if k != 'slate'},
            'quotes': normalized_quotes, 'signals': signals, 'events': list(reversed(events)),
            'freshness': freshness, 'issues': issues, 'worker': packets,
            'performance': scorecard(outcomes), 'calibration': read_json(research/'calibration_latest.json', issues),
            'portfolio_context': read_json(data/'intelligence'/'portfolio.json',issues),
            'data_root': str(data), 'execution_enabled': False}
