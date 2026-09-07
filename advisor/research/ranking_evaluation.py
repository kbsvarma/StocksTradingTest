"""Prospective selected-vs-opportunity-set signal comparison.

Gross reference-bar returns measure ranking, not entry fills or tradable alpha.
Only complete paired populations from committed live releases are evaluated.
"""
import json
from datetime import datetime
from advisor.suggestion_policy import ET, finite
from advisor.research.suggestion_store import atomic_json, committed_ids


def evaluate(research, close):
    releases = []
    for release in committed_ids(research):
        doc = json.loads((research / 'suggestion_releases' / f'{release}.json').read_text())
        releases.append(doc)
    # First committed issue per price-bar/version; retries and intraday
    # revisions cannot manufacture independent observations.
    seen, comparisons, skipped = set(), [], []
    for doc in sorted(releases, key=lambda d: d['as_of']):
        key = (doc['price_bar'], doc['scoring_version'])
        if key in seen:
            continue
        seen.add(key)
        archive = research / 'pick_opportunities' / f'{doc["opportunity_set_id"]}.json'
        try:
            opportunity = json.loads(archive.read_text())
            # Original opportunity hashes use the producer's JSON encoding.
            import hashlib
            encoded = json.dumps(opportunity, sort_keys=True, allow_nan=False)
            if hashlib.sha256(encoded.encode()).hexdigest() != doc['opportunity_set_id']:
                raise ValueError('Opportunity digest mismatch')
            returns = {}
            end_position = 0
            for row in opportunity['eligible']:
                ticker = row['ticker']
                start = close.index.searchsorted(doc['price_bar'], side='right')
                end = start + row['horizon_td'] - 1
                end_position = max(end_position, end)
                if ticker not in close or start == 0 or end >= len(close):
                    raise ValueError('Full opportunity population not matured or covered')
                series = close[ticker].iloc[start - 1:end + 1]
                if series.isna().any() or not all(finite(float(x)) and x > 0 for x in series):
                    raise ValueError('Incomplete opportunity price window')
                returns[ticker] = (-1 if row['direction'] == 'short' else 1) * (float(series.iloc[-1]) / row['ref_px'] - 1) * 100
            selected = opportunity['selected']
            if not returns or not selected or any(t not in returns for t in selected):
                raise ValueError('Empty or incomplete selection')
            chosen = sum(returns[t] for t in selected) / len(selected)
            control = sum(returns.values()) / len(returns)
            comparisons.append({'price_bar': key[0], 'scoring_version': key[1],
                                'end_bar': str(close.index[end_position].date()),
                                'n_eligible': len(returns), 'n_selected': len(selected),
                                'selected_gross_pct': chosen, 'all_eligible_gross_pct': control,
                                'lift_pp': chosen - control})
        except (OSError, ValueError, KeyError) as exc:
            skipped.append({'price_bar': key[0], 'reason': str(exc)})
    versions = {}
    for version in sorted({r['scoring_version'] for r in comparisons}):
        rows = [r for r in comparisons if r['scoring_version'] == version]
        independent, end = [], ''
        for row in rows:
            if row['price_bar'] >= end:
                independent.append(row); end = row['end_bar']
        versions[str(version)] = {'n_paired_issue_dates': len(rows),
            'n_nonoverlapping_windows': len(independent),
            'mean_lift_pp': sum(r['lift_pp'] for r in independent) / len(independent) if independent else None,
            'promotion_allowed': False,
            'status': 'insufficient independent windows' if len(independent) < 30 else 'independent review required'}
    result = {'as_of': datetime.now(ET).isoformat(), 'by_scoring_version': versions,
              'comparisons': comparisons, 'skipped': skipped,
              'method': 'First committed issue per bar/version; full opportunity population; nonoverlapping-window summary',
              'interpretation': 'Gross signal-ranking diagnostic, not cost-complete execution performance or proof of alpha'}
    atomic_json(research / 'ranking_evaluation.json', result)
    return result
