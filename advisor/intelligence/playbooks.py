"""Registered, falsifiable research playbooks. Rules are hypotheses, not alpha."""
from __future__ import annotations

from advisor.intelligence.contract import new_call, number, timestamp, digest
from advisor.intelligence.evidence import audit

PLAYBOOKS = {
    'earnings_continuation': {
        'name': 'Post-earnings continuation', 'version': '1', 'horizon': 15,
        'benchmark': 'sector ETF over identical activated window',
        'required': ['actual_eps', 'consensus_eps', 'prior_guidance', 'new_guidance',
                     'post_event_return_pct', 'sector_return_pct', 'volume_ratio'],
        'dependencies': ['earnings', 'guidance', 'estimate_revision'],
        'invalidation': 'Guidance is withdrawn or the post-event price structure fails'},
    'fundamental_revision': {
        'name': 'Fundamental revision', 'version': '1', 'horizon': 42,
        'benchmark': 'sector ETF and simple revision-only candidate basket',
        'required': ['prior_eps', 'current_eps', 'prior_revenue', 'current_revenue', 'forward_pe'],
        'dependencies': ['guidance', 'estimate_revision', 'filing'],
        'invalidation': 'Forward earnings or revenue revisions reverse'},
    'sector_repricing': {
        'name': 'Sector repricing', 'version': '1', 'horizon': 21,
        'benchmark': 'broad market and equal-weight sector opportunity set',
        'required': ['driver_change_pct', 'earnings_sensitivity', 'sector_return_pct', 'market_return_pct'],
        'dependencies': ['macro', 'guidance', 'price_trigger'],
        'invalidation': 'The causal macro driver reverses or earnings sensitivity is disproved'},
}
THESIS_FIELDS = ('what_changed', 'consensus', 'variant', 'mechanism', 'why_not_priced',
                 'catalyst', 'invalidation', 'contrary_evidence')
UNITS = {
    'actual_eps':'USD/share', 'consensus_eps':'USD/share', 'prior_guidance':'USD/share',
    'new_guidance':'USD/share', 'prior_eps':'USD/share', 'current_eps':'USD/share',
    'prior_revenue':'USD', 'current_revenue':'USD', 'forward_pe':'multiple',
    'post_event_return_pct':'percent', 'sector_return_pct':'percent',
    'market_return_pct':'percent', 'driver_change_pct':'percent',
    'earnings_sensitivity':'earnings_pct_per_driver_pct', 'volume_ratio':'multiple',
}


def operating_model(*, revenue, operating_margin, interest, tax_rate, diluted_shares, multiple):
    values = (revenue, operating_margin, interest, tax_rate, diluted_shares, multiple)
    if not all(number(x) for x in values) or revenue <= 0 or diluted_shares <= 0 or multiple <= 0 or interest < 0 or not -1 <= operating_margin <= 1 or not 0 <= tax_rate < 1:
        raise ValueError('Invalid operating-model assumptions')
    pretax = revenue * operating_margin - interest
    net = pretax * (1 - tax_rate) if pretax > 0 else pretax
    eps = net / diluted_shares
    return {'eps': eps, 'scenario_price': eps * multiple if eps > 0 else None,
            'method': 'Revenue × operating margin less interest and positive-income tax; diluted EPS × assumed multiple',
            'status': 'assumption_sensitivity_not_fair_value',
            'inputs': dict(zip(('revenue', 'operating_margin', 'interest', 'tax_rate', 'diluted_shares', 'multiple'), values))}


def assess(packet, *, as_of):
    spec = PLAYBOOKS.get(packet.get('playbook'))
    if not spec:
        raise ValueError('Unknown playbook')
    claims = packet.get('claims') or []
    evidence = audit(claims, packet.get('sources') or {}, as_of=as_of, author=packet.get('author'))
    blockers = []
    if not evidence['ready']:
        blockers.append('Load-bearing evidence requires independent verification')
    if not evidence['primary_present']:
        blockers.append('Verified primary evidence required')
    by_id = {c.get('claim_id'): c for c in claims}
    observations, values = packet.get('observations') or {}, {}
    for key in spec['required']:
        claim = by_id.get(observations.get(key)) or {}
        if claim.get('kind') != 'numeric' or not number(claim.get('value')):
            blockers.append(f'Missing measured {key}')
        else:
            values[key] = claim['value']
            if claim.get('unit') != UNITS[key]:
                blockers.append(f'{key} requires unit {UNITS[key]}')
    pairs = [('actual_eps','consensus_eps'), ('prior_guidance','new_guidance')] if packet['playbook']=='earnings_continuation' else [('prior_eps','current_eps'),('prior_revenue','current_revenue')] if packet['playbook']=='fundamental_revision' else []
    for first,second in pairs:
        a,b=(by_id.get(observations.get(k)) or {} for k in (first,second))
        if a and b and a.get('period') != b.get('period'):
            blockers.append(f'{first} and {second} must refer to the same forecast/reporting period')
    thesis = packet.get('thesis') or {}
    for key in THESIS_FIELDS:
        if not isinstance(thesis.get(key), str) or len(thesis[key].strip()) < 12:
            blockers.append(f'Underwrite {key.replace("_", " ")}')
    try:
        event = timestamp(packet['event_at'])
        if event > timestamp(as_of) and packet['playbook'] == 'earnings_continuation':
            blockers.append('Earnings have not occurred')
        if packet['playbook'] == 'earnings_continuation' and (timestamp(as_of)-event).total_seconds() > 10*86400:
            blockers.append('Earnings event is outside the registered 10-calendar-day discovery window')
        if event >= timestamp(packet['expires_at']):
            blockers.append('Catalyst outside call horizon')
        if packet['playbook'] == 'earnings_continuation':
            consensus = by_id.get(observations.get('consensus_eps')) or {}
            source = (packet.get('sources') or {}).get(consensus.get('source_id')) or {}
            if timestamp(source['retrieved_at']) >= event:
                blockers.append('Consensus must be captured before the earnings event')
    except (KeyError, ValueError, TypeError):
        blockers.append('Verified event and pre-event timestamps required')
    diagnostics = {}
    cost=packet.get('cost') or {}
    try:
        cost_age=(timestamp(as_of)-timestamp(cost['as_of'])).total_seconds()
        cost_ok=number(cost.get('round_trip_bps')) and 0 <= cost['round_trip_bps'] <= 1000 and bool(cost.get('source')) and 0 <= cost_age <= 72*3600
    except (KeyError,TypeError,ValueError):cost_ok=False
    if packet.get('plan') and not cost_ok:
        blockers.append('Current sourced round-trip cost model required')
    if all(k in values for k in spec['required']):
        v = values
        if packet['playbook'] == 'earnings_continuation':
            diagnostics = {'eps_surprise_pct': 100 * (v['actual_eps'] - v['consensus_eps']) / abs(v['consensus_eps']) if v['consensus_eps'] else None,
                           'relative_reaction_pp': v['post_event_return_pct'] - v['sector_return_pct'],
                           'guidance_delta': v['new_guidance'] - v['prior_guidance']}
            if v['actual_eps'] <= v['consensus_eps'] or v['new_guidance'] <= v['prior_guidance']:
                blockers.append('Positive earnings and guidance revision not established')
            if diagnostics['relative_reaction_pp'] <= 0 or v['volume_ratio'] < 1.2:
                blockers.append('Post-event relative-price and volume confirmation absent')
        elif packet['playbook'] == 'fundamental_revision':
            diagnostics = {'eps_revision_pct': 100 * (v['current_eps'] - v['prior_eps']) / abs(v['prior_eps']) if v['prior_eps'] else None,
                           'revenue_revision_pct': 100 * (v['current_revenue'] - v['prior_revenue']) / abs(v['prior_revenue']) if v['prior_revenue'] else None}
            if v['current_eps'] <= v['prior_eps'] or v['current_revenue'] <= v['prior_revenue'] or v['forward_pe'] <= 0:
                blockers.append('Earnings/revenue revision and valuation inputs do not support this playbook')
        else:
            diagnostics = {'implied_earnings_change_pct': v['driver_change_pct'] * v['earnings_sensitivity'],
                           'sector_relative_return_pp': v['sector_return_pct'] - v['market_return_pct']}
            if diagnostics['implied_earnings_change_pct'] <= 0:
                blockers.append('Driver sensitivity does not support a positive earnings change')
    scenario_analysis={}
    if packet.get('scenario'):
        from advisor.intelligence.forecasts import scenario_payoff
        try:
            plan=packet.get('plan') or {}
            scenario_analysis=scenario_payoff(entry=(plan['entry_low']+plan['entry_high'])/2,
                scenarios=packet['scenario']['scenarios'],round_trip_bps=packet['cost']['round_trip_bps'])
            if scenario_analysis['weighted_net_return_pct']<=0:
                blockers.append('Underwritten scenarios have nonpositive modeled net payoff')
        except (KeyError,ValueError,TypeError):
            blockers.append('Scenario payoff inputs are incomplete or invalid')
    return {'ready_for_review': not blockers, 'blockers': blockers, 'diagnostics': diagnostics,
            'scenario_analysis':scenario_analysis,
            'evidence': evidence, 'playbook': spec,
            'status': 'research_hypothesis_not_validated_strategy'}


def underwrite(packet, *, as_of):
    result = assess(packet, as_of=as_of)
    spec = result['playbook']
    plan = packet.get('plan') or {}
    scenario_analysis=result['scenario_analysis']
    call = new_call(ticker=packet['ticker'], episode=packet['episode'],
        issued_at=as_of, expires_at=packet['expires_at'], origin='underwriting',
        instrument_type=packet.get('instrument_type', 'equity'),
        playbook=packet['playbook'], model_version=spec['version'],
        status='review_required' if result['ready_for_review'] and plan else 'candidate',
        action='enter_long' if plan else 'watch',
        plan={**plan, 'horizon_sessions': plan.get('horizon_sessions', spec['horizon'])} if plan else {},
        thesis=packet.get('thesis') or {}, claims=packet.get('claims') or [],
        observations=packet.get('observations') or {}, event_at=packet.get('event_at'),
        dependencies=[{'ticker': packet['ticker'], 'kind': kind} for kind in spec['dependencies']],
        blockers=result['blockers'] + ([] if plan else ['Thesis-derived entry/exit plan required']),
        diagnostics=result['diagnostics'], evidence_audit=result['evidence'],
        source_snapshot=packet.get('sources') or {}, packet_hash=digest(packet),
        author=packet.get('author'), scenario=packet.get('scenario') or {},
        cost=packet.get('cost') or {}, allocation=packet.get('allocation') or {},
        scenario_analysis=scenario_analysis,
        drivers=packet.get('drivers') or [],
        legacy_id=packet.get('legacy_id'), publication_lineage=packet.get('publication_lineage') or {},
        parent_call_id=packet.get('parent_call_id'),
        benchmark=packet.get('benchmark') or spec['benchmark'],
        sector=packet.get('sector', 'Unclassified'))
    return call
