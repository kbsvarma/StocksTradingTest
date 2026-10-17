import copy
import pytest
from advisor.investigator.grounding import hydrate, valuation_statement, output_schema
from advisor.investigator.runtime import validate
from advisor.investigator.reasoner import SYNTHESIS_SCHEMA


def test_primary_release_preserves_adjustments_before_search_commentary(monkeypatch):
    from advisor.investigator.reasoner import evidence_context
    monkeypatch.setenv('ADVISOR_RESEARCH_PROVIDER','ollama')
    body='Results overview. '*45+'A nonrecurring investment gain affected reported profit. '+'Business discussion. '*220
    rows=[{'id':'commentary','kind':'document','authority':'reported','temporal':{'state':'current'},
           'payload':{'document_class':'earnings_release','text':'A summary repeated the profit headline.'}},
          {'id':'release','kind':'document','authority':'primary','temporal':{'state':'current'},
           'payload':{'document_class':'earnings_release','text':body}}]
    selected=evidence_context(rows,limit=7500)
    assert selected[0]['id']=='release'
    assert 'nonrecurring investment gain' in selected[0]['payload']['text']


@pytest.mark.parametrize('stage',['synthesize','revise'])
def test_cloud_providers_cannot_bypass_numerical_condition_audit(monkeypatch,stage):
    from advisor.investigator import reasoner
    monkeypatch.setenv('ADVISOR_RESEARCH_PROVIDER','gemini')
    def model(*args,**kwargs):
        return {'insights':[],'entry_conditions':['Enter when FCF yield exceeds 2.5%.'],'exit_conditions':[]},{}
    if stage=='synthesize':value,_=reasoner.synthesize('TEST',[],{'findings':[]},runner=model)
    else:value,_=reasoner.revise('TEST',{'insights':[]},{},[],{'findings':[]},runner=model)
    assert value['_condition_errors']


def test_fiscal_year_labels_are_not_arbitrary_thresholds():
    from advisor.investigator.grounding import condition_errors
    assert not condition_errors({'entry_conditions':['First quarter of FY27 segment disclosures demonstrates gross margin stability.']},{})
    assert not condition_errors({'entry_conditions':['Meets the dated guidance for third quarter fiscal 2027 revenue.']},{})
    assert not condition_errors({'entry_conditions':['Meets the dated guidance for fiscal year 2027 revenue.']},{})
    assert condition_errors({'entry_conditions':['Revenue growth falls below mid-teens.']},{})


def test_provider_generated_investor_beliefs_are_replaced_by_explicit_valuation_basis():
    from advisor.investigator.grounding import audit_generated
    result=audit_generated({'insights':[{'what_is_priced_in':'Investors expect permanent growth.'}], 'entry_conditions':[]},{})
    assert 'Investors expect' not in result['insights'][0]['what_is_priced_in']


def test_dated_bank_conditions_distinguish_identifiers_from_invented_cutoffs():
    from advisor.investigator.grounding import condition_errors
    proposal={'entry_conditions':['Price reaches the 50-day moving average of $349.63 as of September 4, 2026.',
        'Net interest income meets full-year 2026 guidance.'],
        'exit_conditions':['Standardized CET1 capital ratio falls below regulatory minimums.']}
    assert not condition_errors(proposal,{'technicals':{'ma50':349.633}})
    assert condition_errors({'exit_conditions':['CET1 falls below 12.5%.']},{})
    assert condition_errors({'entry_conditions':['Price crosses the moving average of $400.00 as of September 4, 2026.']},{'technicals':{'ma50':349.633}})


def test_computed_observations_and_sources_are_bound_without_mutating_input():
    rows=[{'id':'current','kind':'fundamental','payload':{'value':120},'temporal':{'state':'current'}},
          {'id':'prior','kind':'fundamental','payload':{'value':100},'temporal':{'state':'context_only'}}]
    analysis={'findings':[{'id':'growth','detail':'Revenue grew 20% year over year.', 'evidence_ids':['current','prior']}]}
    proposal={'action_reason':'Growth needs valuation context.','insights':[{'finding_ids':['growth'],'evidence':[]}]}
    before=copy.deepcopy(proposal)
    result=hydrate(proposal,analysis,rows)
    assert proposal==before
    assert result['insights'][0]['what_changed']=='Revenue grew 20% year over year.'
    assert [c['use'] for c in result['insights'][0]['evidence']]==['current','historical_comparison']
    assert result['insights'][0]['evidence'][0]['excerpt']=='"value": 120'
    proposal['insights'][0]['finding_ids']=['invented']
    with pytest.raises(ValueError,match='Unknown'):hydrate(proposal,analysis,rows)


def test_valuation_preserves_horizon_and_assumptions():
    text=valuation_statement({'implied_growth_sensitivity':[{'required_growth_pct':30.85,'discount_rate':.1,'terminal_growth':.03,'years':5}]})
    assert 'over 5 years' in text and '3% terminal growth' in text
    assert 'not observed investor beliefs' in text and 'free-cash-flow' in text


def test_model_cannot_supply_calculated_fields_or_invent_empty_findings():
    schema=output_schema(SYNTHESIS_SCHEMA,{})
    insight=schema['properties']['insights']['items']
    assert 'what_changed' not in insight['properties']
    assert 'what_is_priced_in' not in insight['properties']
    with pytest.raises(ValueError,match='array outside'):validate(['fake'],insight['properties']['finding_ids'])
    validate('2 quarters',insight['properties']['horizon'])
    validate('insight_1',insight['properties']['id'])
    assert SYNTHESIS_SCHEMA['properties']['action_reason']=={'type':'string'}


def test_sector_passages_prioritize_bank_drivers_over_generic_boilerplate():
    from advisor.investigator.reasoner import passages
    text=('Introduction. '*80+'guarantees '+'general boilerplate '*200+
          'Net interest income increased with asset yields. '+'details '*200+
          'Common equity Tier 1 capital ratio remained above requirements. '+'details '*200)
    excerpt=passages(text,1200,('net interest income','common equity tier 1'))
    assert 'Net interest income increased' in excerpt
    assert 'Common equity Tier 1' in excerpt


def test_document_passage_keeps_causal_sentence_subject():
    from advisor.investigator.reasoner import passages
    sentence='Cash provided by operations increased due to higher revenue, partially offset by an increase in receivables due to extended payment terms with customers.'
    text='Introduction. '+'Earlier discussion. '*100+sentence+' Other discussion. '*200
    assert sentence in passages(text,1200)


def test_numerical_conditions_need_computed_basis_not_model_approval():
    from advisor.investigator.grounding import condition_errors
    assert condition_errors({'entry_conditions':['Cash conversion above 0.8 times net income']},{})
    assert condition_errors({'entry_conditions':['FCF growth exceeds twenty five percent']},{})
    assert not condition_errors({'entry_conditions':['Collections improve over the next 2 quarters']},{})
    assert not condition_errors({'entry_conditions':['Close above the prior high of 517.78']},{'technicals':{'prior_20_high':517.783}})
    assert not condition_errors({'entry_conditions':['Close above the prior high of 517.783']},{'technicals':{'prior_20_high':517.783}})
    assert condition_errors({'entry_conditions':['Close above the prior high of 550.00']},{'technicals':{'prior_20_high':517.783}})
    from advisor.investigator.reasoner import validate_synthesis
    assert validate_synthesis({'insights':[],'_condition_errors':['Unverified cutoff']},[])[1][0]['id']=='decision_conditions'


def test_low_materiality_context_is_summarized_without_altering_full_scan():
    import json
    from advisor.investigator.reasoner import local_packet
    analysis={'fundamentals':{'inventory':10,'inventory_yoy_pct':99,'revenue':1000},'estimates':{'0y_eps_revision_30d_pct':.2},
        'findings':[{'id':'inventory_divergence','materiality':1},{'id':'estimate_revision','materiality':1}]}
    before=copy.deepcopy(analysis)
    _,_,packet,_=local_packet([],analysis)
    selected=json.loads(packet)
    assert 'inventory' not in selected['quarterly_and_balance_sheet_metrics'] and 'estimates' not in selected
    assert '1.00%' in selected['context_only_notes'][0]
    assert analysis==before


def test_document_quotes_are_closed_and_bound_to_original_passages():
    from advisor.investigator.reasoner import document_quote_options, local_synthesis_schema
    row={'id':'E01','kind':'document','temporal':{'state':'current'},'payload':{'text':
        'Cash collections increased with revenue.\n[passage boundary]\nReceivables increased due to extended payment terms.'}}
    choices=document_quote_options(row)
    assert len(choices)==2
    assert all(q in row['payload']['text'] and 'boundary' not in q for q in choices)
    schema=local_synthesis_schema([row])['properties']['insights']['items']['properties']['evidence']['items']
    validate({'source_id':'E01','excerpt':choices[1],'use':'current'},schema)
    with pytest.raises(ValueError):
        validate({'source_id':'E01','excerpt':'Required growth is 21 percent.','use':'current'},schema)


def test_long_document_quote_options_preserve_exact_source_substrings():
    from advisor.investigator.reasoner import document_quote_options
    text='Cash flows '+('continued because of contractual customer terms '*40)+'.'
    choices=document_quote_options({'payload':{'text':text}})
    assert len(choices)>1
    assert all(8<=len(q)<=600 and q in text for q in choices)


def test_missing_observation_reaches_revision_as_rejection_not_success():
    from advisor.investigator.reasoner import validate_synthesis
    result=hydrate({'insights':[{'id':'empty','evidence':[],'finding_ids':[]}]},{},[])
    accepted,rejected=validate_synthesis(result,[])
    assert not accepted
    assert any('calculated observation' in reason for reason in rejected[0]['reasons'])


def test_local_draft_selects_grounding_before_authoring_its_conclusion():
    schema=output_schema(SYNTHESIS_SCHEMA,{})
    fields=list(schema['properties']['insights']['items']['properties'])
    assert fields.index('finding_ids')<fields.index('mechanism')
    assert fields.index('evidence')<fields.index('mechanism')


def test_passage_references_bind_source_text_and_reject_cross_source_choices():
    from advisor.investigator.reasoner import local_synthesis_schema,expand_local_references
    rows=[{'id':'E01','kind':'document','payload':{'text':'Collections increased with sales. Terms were extended for large customers.'},'temporal':{'state':'current'}},
          {'id':'E02','kind':'document','payload':{'text':'Historical sales were lower.'},'temporal':{'state':'context_only'}}]
    schema=local_synthesis_schema(rows,references=True)['properties']['insights']['items']['properties']['evidence']['items']
    citation={'source_id':'E01','passage_id':'P02','use':'current'}
    validate(citation,schema)
    proposal={'insights':[{'evidence':[citation]}]}
    expanded=expand_local_references(proposal,rows)
    assert expanded['insights'][0]['evidence'][0]['excerpt']=='Terms were extended for large customers.'
    assert 'passage_id' in citation
    with pytest.raises(ValueError):validate({**citation,'source_id':'E02'},schema)
    with pytest.raises(ValueError):expand_local_references({'insights':[{'evidence':[{**citation,'source_id':'E02'}]}]},rows)
    with pytest.raises(ValueError):validate({'source_id':'E02','passage_id':'P01','use':'current'},schema)


def test_required_derived_records_are_not_displaced_by_metadata_budget(monkeypatch):
    from advisor.investigator.reasoner import evidence_context
    monkeypatch.setenv('ADVISOR_RESEARCH_PROVIDER','ollama')
    rows=[{'id':f'f{n}','ticker':'TEST','kind':'fundamental','source':'sec_facts','payload':{'metric':'cfo','value':n+1,'unit':'USD','duration_class':'quarter','lineage':('auditable parent metadata '*100)},'temporal':{'state':'current'},'title':'Derived quarter','url':'https://www.sec.gov/facts'} for n in range(12)]
    selected=evidence_context(rows,limit=4000,required_ids=[r['id'] for r in rows])
    assert {r['id'] for r in selected}=={r['id'] for r in rows}
    with pytest.raises(ValueError,match='Required evidence'):evidence_context(rows,limit=500,required_ids=[r['id'] for r in rows])
