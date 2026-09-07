"""Reviewed thesis plans override the benchmark only with explicit geometry.

No unvalidated strategy-specific stop multipliers are silently promoted.
"""
from advisor.suggestion_policy import finite


def plan_for(direction, px, atr, evidence):
    sign = -1 if direction == 'short' else 1
    baseline = {'entry_low': px - .25 * atr, 'entry_high': px + .25 * atr,
                'stop': px - sign * 1.5 * atr, 'target': px + sign * 3 * atr,
                'horizon_td': 21,
                'template': {'id': 'atr_baseline_v1', 'validated': False,
                             'purpose': 'common signal benchmark; not thesis-derived fair value'}}
    thesis = evidence.get('thesis') or {}
    custom = thesis.get('plan')
    if not custom:
        return baseline
    required = ('entry_low', 'entry_high', 'stop', 'target')
    if thesis.get('direction') != direction or not all(finite(custom.get(k)) and custom[k] > 0 for k in required):
        raise ValueError('Reviewed plan has invalid direction or price levels')
    lo, hi, stop, target = (custom[k] for k in required)
    valid = stop < lo <= hi < target if direction == 'long' else target < lo <= hi < stop
    horizon = custom.get('horizon_td')
    if not valid or not isinstance(horizon, int) or isinstance(horizon, bool) or not 1 <= horizon <= 126:
        raise ValueError('Reviewed plan has invalid geometry or horizon')
    if not thesis.get('horizon_rationale') or not thesis.get('invalidation'):
        raise ValueError('Reviewed plan requires horizon rationale and invalidation')
    return {**{k: custom[k] for k in required}, 'horizon_td': horizon,
            'template': {'id': 'reviewed_thesis_v1', 'validated': False,
                         'reviewer': thesis['reviewer'], 'reviewed_at': thesis['reviewed_at'],
                         'purpose': 'reviewed scenario plan; profitability is not established'}}


def expiry_for(price_bar, horizon):
    import exchange_calendars as xc
    cal = xc.get_calendar('XNYS')
    session = cal.date_to_session(price_bar, direction='previous')
    return str(cal.session_offset(session, horizon).date())
