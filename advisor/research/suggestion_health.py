"""Read-only suggestion SLO and release-integrity checks."""
import json
from datetime import datetime
from advisor.suggestion_policy import ET, release_freshness
from advisor.research.suggestion_store import committed_ids, digest


def assess(research, now=None):
    now = now or datetime.now(ET)
    reasons, warnings = [], []
    def read(name):
        try:
            return json.loads((research / name).read_text())
        except (OSError, ValueError):
            return {}
    doc = read('picks_latest.json')
    if not doc:
        reasons.append('No suggestion release')
    else:
        reasons.extend(release_freshness(doc, now)['reasons'])
        try:
            if not doc.get('release_id') or digest({k: v for k, v in doc.items() if k != 'release_id'}) != doc['release_id']:
                raise ValueError('latest release digest mismatch or legacy release')
            committed_ids(research)
        except (OSError, ValueError) as exc:
            reasons.append(f'Release integrity failure: {exc}')
        if not doc.get('n_priority'):
            warnings.append('No priority research ideas; inspect candidate blockers')
    for name in ('nightly_status.json', 'suggestion_health.json'):
        state = read(name)
        if state.get('status') == 'failed':
            reasons.append(f'{name}: latest refresh failed')
        elif state.get('status') == 'degraded':
            warnings.append(f'{name}: degraded research coverage')
    return {'as_of': now.isoformat(), 'status': 'failed' if reasons else 'degraded' if warnings else 'healthy',
            'ok': not reasons, 'reasons': reasons, 'warnings': warnings,
            'release_id': doc.get('release_id'),
            'slo': {'freshness': 'latest completed XNYS session, 30-minute bar-finalization allowance',
                    'integrity': 'hashed immutable release chain and durable issue records'}}


def check_alert(state, research, send, now=None):
    """Notify on state changes only; failed delivery is retried on next check."""
    report = assess(research, now)
    # Degraded coverage remains visible, without paging for an empty priority list.
    condition = '; '.join(report['reasons']) if not report['ok'] else 'recovered'
    previous = state.get('suggestion_condition')
    if condition != previous and (not report['ok'] or previous not in (None, 'recovered')):
        message = 'Advisor suggestions: ' + condition
        if send(message) is False:
            return report
    state['suggestion_condition'] = condition
    return report
