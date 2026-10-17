"""Deterministic quality classification for saved model investigations.

This never turns model prose into an actionable call. It only answers whether
a saved report has the minimum evidence needed to enter human underwriting.
"""
from __future__ import annotations

CORE_DIRECTIONAL_COVERAGE = (
    'valuation_calculation', 'current_results', 'provider_estimates',
    'dated_catalyst',
)


def assess(report: dict) -> dict:
    synthesis=report.get('synthesis') or {}
    action=synthesis.get('action')
    directional=action in {'buy_candidate','avoid_new_entry'}
    coverage=synthesis.get('research_coverage') or {}
    missing=sorted(name for name,value in coverage.items() if value is not True)
    missing_core=sorted(name for name in CORE_DIRECTIONAL_COVERAGE
                        if coverage.get(name) is not True) if directional else []
    source_failures=[]
    for name,row in (report.get('collection') or {}).items():
        if row.get('status') in {'failed','partial'}:
            source_failures.append({'source':name,'status':row.get('status'),
                                    'errors':list(row.get('errors') or [])[:5]})
    review=synthesis.get('review_status')
    if review not in {'source_linked_brief','model_challenged'}:
        status='blocked';reason='No source-linked model synthesis passed validation'
    elif missing_core:
        status='blocked';reason='Directional thesis lacks required decision coverage: '+', '.join(missing_core)
    elif missing or source_failures:
        status='degraded';reason='Research can enter review, but material source gaps remain'
    else:
        status='review_ready';reason='Evidence minimum met for independent human underwriting'
    return {'status':status,'reason':reason,'action':action,'directional':directional,
            'missing_coverage':missing,'missing_core_coverage':missing_core,
            'source_failures':source_failures,'actionable':False,
            'approval_required':True}
