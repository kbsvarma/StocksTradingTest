"""Decision-quality attribution with explicit sample and cost coverage."""
from __future__ import annotations
from collections import defaultdict
import random
from datetime import datetime, timezone
from advisor.intelligence.contract import number, timestamp
from advisor.intelligence.forecasts import wilson


def observed_outcome(call):
    entry, exit_px = call.get('entry_observed_px'), call.get('exit_observed_px')
    cost = (call.get('cost') or {}).get('round_trip_bps')
    if not number(entry) or entry <= 0 or not number(exit_px) or exit_px <= 0:
        return {'status': 'not_scorable', 'reason': 'Observed entry and exit required'}
    gross = (exit_px / entry - 1) * 100
    net = gross - cost / 100 if number(cost) and cost >= 0 else None
    return {'status': 'resolved', 'gross_return_pct': gross, 'net_return_pct': net,
            'cost_complete': net is not None, 'basis': 'quote_observation_with_modeled_costs_not_actual_execution',
            'win': int(net > 0) if net is not None else None}


def scorecard(rows, *, seed=42):
    """Rows must use the exact activated-window benchmark and original episode."""
    seen, eligible, coverage = set(), [], defaultdict(int)
    for r in sorted(rows, key=lambda x: (x.get('issued_at', ''), x.get('revision', 1))):
        key = r.get('episode_id')
        if not key or key in seen:
            coverage['duplicates_or_missing_identity'] += 1; continue
        seen.add(key)
        coverage['episodes'] += 1
        if r.get('source') != 'prospective': coverage['replay_excluded'] += 1; continue
        if not r.get('activated'): coverage['not_activated'] += 1; continue
        coverage['activated'] += 1
        if r.get('status') != 'resolved': coverage['unresolved'] += 1; continue
        if not r.get('cost_complete') or not number(r.get('net_return_pct')):
            coverage['missing_costs_or_return'] += 1; continue
        eligible.append(r)
    cohorts = defaultdict(list)
    for r in eligible:
        key = (r.get('playbook', 'unknown'), r.get('model_version', 'unknown'), r.get('basis', 'unknown'))
        cohorts[key].append(r)
    groups = []
    for key, population in sorted(cohorts.items()):
        n = len(population); wins = sum(r['net_return_pct'] > 0 for r in population)
        paired = [r for r in population if number(r.get('benchmark_return_pct')) and r.get('benchmark_window') == 'same_activated_window']
        # Bootstrap issue-date clusters; never count correlated same-day ideas
        # as independent observations. Interval is descriptive at small N.
        clusters = defaultdict(list)
        for r in paired:
            clusters[r['issued_at'][:10]].append(r['net_return_pct'] - r['benchmark_return_pct'])
        means = [sum(v)/len(v) for v in clusters.values()]
        interval = None
        if len(means) >= 5:
            rng = random.Random(seed)
            samples = sorted(sum(rng.choices(means, k=len(means)))/len(means) for _ in range(1000))
            interval = [samples[24], samples[974]]
        groups.append({'playbook': key[0], 'model_version': key[1], 'basis': key[2],
            'n_resolved': n, 'hit_rate': wins/n, 'hit_rate_interval': wilson(wins, n),
            'mean_net_pct': sum(r['net_return_pct'] for r in population)/n,
            'worst_call_pct': min(r['net_return_pct'] for r in population),
            'n_benchmark_pairs': len(paired), 'n_issue_date_clusters': len(means),
            'mean_excess_pp': sum(means)/len(means) if means else None,
            'excess_interval': interval, 'promotion_allowed': False,
            'note': 'No portfolio compounding or drawdown inferred from overlapping calls; matched-date cluster uncertainty'} )
    return {'coverage': dict(coverage), 'groups': groups, 'promotion_allowed': False,
            'activation_rate': coverage['activated']/coverage['episodes'] if coverage['episodes'] else None}


def portfolio_impact(call, portfolio, *, now=None):
    now = now or datetime.now(timezone.utc)
    blockers = []
    try:
        age = (now - timestamp(portfolio['as_of'])).total_seconds()
        if portfolio.get('status') != 'verified' or not 0 <= age <= 86400:
            blockers.append('Verified portfolio snapshot within 24 hours required')
    except (ValueError, KeyError, TypeError): blockers.append('Portfolio timestamp unavailable')
    holdings = portfolio.get('holdings') or []
    if any(not number(h.get('weight_pct')) for h in holdings): blockers.append('Holding weights unavailable')
    weight = (call.get('allocation') or {}).get('weight_pct')
    if not number(weight) or not 0 < weight <= 100: blockers.append('Explicit proposed allocation required')
    if blockers:
        return {'scope': 'model_book_only', 'blockers': blockers, 'sizing_available': False}
    sector = call.get('sector')
    if not sector or sector == 'Unclassified' or any(not h.get('sector') for h in holdings):
        blockers.append('Complete sector mapping required')
    existing_name = sum(h['weight_pct'] for h in holdings if h.get('ticker') == call['ticker'])
    sector_weight = sum(h['weight_pct'] for h in holdings if h.get('sector') == sector)
    gross = sum(abs(h['weight_pct']) for h in holdings)
    mandate = portfolio.get('mandate') or {}
    for value, cap, label in ((existing_name + weight, mandate.get('max_name_pct'), 'Name concentration'),
                              (sector_weight + weight, mandate.get('max_sector_pct'), 'Sector concentration'),
                              (gross + weight, mandate.get('max_gross_pct'), 'Gross exposure')):
        if not number(cap): blockers.append(f'{label} mandate missing')
        elif value > cap: blockers.append(f'{label} exceeds mandate ({value:.1f}% > {cap:.1f}%)')
    shared = sorted(set(call.get('drivers') or []) & {d for h in holdings for d in h.get('drivers', [])})
    return {'scope': 'verified_portfolio', 'name_after_pct': existing_name + weight,
            'sector_after_pct': sector_weight + weight, 'gross_after_pct': gross + weight,
            'shared_drivers': shared, 'blockers': blockers, 'sizing_available': not blockers,
            'note': 'Exposure diagnostic; no automatic sizing or order permission'}
