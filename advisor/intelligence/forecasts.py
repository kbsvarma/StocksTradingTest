"""Scenario economics and time-held-out cohort diagnostics; no promotion by count."""
from __future__ import annotations
import math
from advisor.intelligence.contract import number, digest, timestamp

EVENT = 'positive_net_return_at_policy_exit'


def scenario_payoff(*, entry, scenarios, round_trip_bps, adverse_shift_pct=2.0):
    if not number(entry) or entry <= 0 or not number(round_trip_bps) or round_trip_bps < 0 or not number(adverse_shift_pct) or adverse_shift_pct < 0:
        raise ValueError('Valid entry and nonnegative costs/stress required')
    if len(scenarios) < 3 or not {'adverse', 'base', 'favorable'} <= {s.get('name') for s in scenarios}:
        raise ValueError('At least adverse, base and favorable scenarios required')
    if len({s['name'] for s in scenarios}) != len(scenarios):
        raise ValueError('Duplicate scenario names')
    if any(not number(s.get('exit_px')) or s['exit_px'] <= 0 or not number(s.get('weight')) or not 0 <= s['weight'] <= 1 for s in scenarios):
        raise ValueError('Invalid scenario prices or weights')
    if abs(sum(s['weight'] for s in scenarios) - 1) > 1e-8:
        raise ValueError('Scenario weights must sum to one')
    rows = [{**s, 'net_return_pct': 100 * (s['exit_px'] / entry - 1) - round_trip_bps / 100} for s in scenarios]
    expected = sum(r['weight'] * r['net_return_pct'] for r in rows)
    return {'scenarios': rows, 'weighted_net_return_pct': expected,
            'stress_net_return_pct': expected - adverse_shift_pct,
            'worst_scenario_pct': min(r['net_return_pct'] for r in rows),
            'round_trip_bps': round_trip_bps, 'entry': entry,
            'basis': 'analyst_scenario_weights', 'calibrated': False,
            'note': 'Weighted scenarios are assumptions, not a validated expected return; adverse gaps may exceed the modeled loss.'}


def wilson(wins, n):
    if not n: return None
    p, z = wins / n, 1.96
    center = (p + z*z/(2*n)) / (1 + z*z/n)
    radius = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1 + z*z/n)
    return [max(0, center-radius), min(1, center+radius)]


def cohort(row):
    return tuple(row.get(k) for k in ('playbook', 'model_version', 'instrument_type', 'horizon_sessions', 'forecast_event'))


def evaluate_forecasts(rows, *, split_at):
    """Training base rate vs strictly later held-out forecasts, clustered by episode.

    Resolving before the split is required for training: issue-time alone leaks
    outcomes across the holdout boundary. Never mix forecast events or versions.
    """
    split = timestamp(split_at)
    seen, eligible, excluded = set(), [], []
    for r in sorted(rows, key=lambda x: x.get('issued_at', '')):
        try:
            key = r['episode_id']
            if key in seen: raise ValueError('Repeated episode')
            seen.add(key)
            if r.get('source') != 'prospective' or r.get('cost_complete') is not True:
                raise ValueError('Only prospective cost-complete observations')
            if r.get('forecast_event') != EVENT or not number(r.get('p_win')) or not 0 <= r['p_win'] <= 1 or r.get('win') not in (0, 1):
                raise ValueError('Forecast event or value unavailable')
            if any(x is None for x in cohort(r)):
                raise ValueError('Incomplete cohort')
            issued, resolved = timestamp(r['issued_at']), timestamp(r['resolved_at'])
            if resolved <= issued: raise ValueError('Resolution must follow issue')
            eligible.append(r)
        except (KeyError, ValueError, TypeError) as exc:
            excluded.append({'episode_id': r.get('episode_id'), 'reason': str(exc)})
    groups = []
    for c in sorted({cohort(r) for r in eligible}, key=str):
        population = [r for r in eligible if cohort(r) == c]
        train = [r for r in population if timestamp(r['resolved_at']) < split]
        test = [r for r in population if timestamp(r['issued_at']) >= split]
        spanning = len(population) - len(train) - len(test)
        base = sum(r['win'] for r in train) / len(train) if train else None
        # Greedy nonoverlapping holdout windows avoid manufactured independence.
        independent, end = [], split
        for r in sorted(test, key=lambda x: timestamp(x['issued_at'])):
            if timestamp(r['issued_at']) >= end:
                independent.append(r); end = timestamp(r['resolved_at'])
        n = len(independent)
        brier = sum((r['p_win'] - r['win'])**2 for r in independent) / n if n else None
        baseline = sum((base - r['win'])**2 for r in independent) / n if n and base is not None else None
        groups.append({'cohort': dict(zip(('playbook', 'model_version', 'instrument_type', 'horizon_sessions', 'forecast_event'), c)),
                       'n_train': len(train), 'n_holdout': len(test), 'n_independent': n,
                       'n_spanning_excluded': spanning, 'training_base_rate': base,
                       'brier': brier, 'baseline_brier': baseline,
                       'brier_skill': 1 - brier/baseline if baseline and brier is not None else None,
                       'hit_rate_interval': wilson(sum(r['win'] for r in independent), n),
                       'promotion_allowed': False,
                       'status': 'insufficient evidence' if n < 30 or len(train) < 30 else 'independent review required'})
    result = {'schema_version': 1, 'split_at': split_at, 'groups': groups, 'excluded': excluded,
              'method': 'Time-held-out forecasts against training base rate, distinct cohorts and nonoverlapping holdout windows'}
    return {**result, 'evaluation_id': digest(result)}
