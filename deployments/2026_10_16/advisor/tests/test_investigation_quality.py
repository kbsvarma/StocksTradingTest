from advisor.investigator.quality import assess


def report(action='buy_candidate',coverage=None,collection=None,review='source_linked_brief'):
    return {'synthesis':{'action':action,'review_status':review,'research_coverage':coverage or {}},
            'collection':collection or {}}


def test_directional_thesis_requires_core_decision_coverage():
    result=assess(report(coverage={'current_results':True}))
    assert result['status']=='blocked'
    assert 'valuation_calculation' in result['missing_core_coverage']
    assert result['actionable'] is False


def test_partial_source_is_visible_even_when_core_coverage_exists():
    coverage={name:True for name in ('valuation_calculation','current_results','provider_estimates','dated_catalyst')}
    result=assess(report(coverage=coverage,collection={'issuer_ir':{'status':'partial','errors':['timeout']}}))
    assert result['status']=='degraded'
    assert result['source_failures'][0]['source']=='issuer_ir'


def test_complete_report_is_only_review_ready_not_actionable():
    coverage={name:True for name in ('valuation_calculation','current_results','provider_estimates','dated_catalyst','current_transcript')}
    result=assess(report(coverage=coverage))
    assert result['status']=='review_ready'
    assert result['approval_required'] and not result['actionable']


def test_unvalidated_synthesis_is_blocked():
    assert assess(report(action='hold',review='not_run'))['status']=='blocked'
