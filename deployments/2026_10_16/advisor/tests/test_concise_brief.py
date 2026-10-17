import json
import pytest
from advisor.investigator.brief import generate,packet


def rows():
    return [{'id':str(i),'ticker':'TEST','kind':'document','authority':'primary','temporal':{'state':'current'},
             'url':f'https://example.com/{i}','published_at':'2026-09-01','payload':{'document_class':kind,
             'text':'Company quarterly financial disclosure. '*40}} for i,kind in enumerate(('earnings_release','earnings_call'))]


def test_report_requires_current_original_sources_and_known_citations():
    records=rows();config={'ADVISOR_RESEARCH_PROVIDER':'gemini','ADVISOR_RESEARCH_MODEL':'gemma-4-31b-it','GEMINI_API_KEY':'private-key'}
    value={'summary':'A company-specific investment interpretation for the report.','action':'hold',
           'report_markdown':'Business evidence and interpretation. '*35+'[Release](https://example.com/0) [Call](https://example.com/1)'}
    class Reply:
        status_code=200
        def json(self):return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'Decision: HOLD\nSummary: '+value['summary']+'\n\n'+value['report_markdown']}]}}]}
    def post(url,**kwargs):
        assert 'private-key' not in url
        assert kwargs['json']['generationConfig']['thinkingConfig']['thinkingLevel']=='MINIMAL'
        return Reply()
    result,usage=generate('TEST',records,{},post=post,config=config)
    assert result['review_status']=='source_linked_brief'
    assert 'adversarial' in result['validation_basis']
    value['report_markdown']+=' [Invented](https://wrong.example/evidence)'
    with pytest.raises(ValueError,match='citations'):generate('TEST',records,{},post=post,config=config)
    records[0]['temporal']['state']='context_only'
    with pytest.raises(ValueError,match='two current'):packet('TEST',records,{})


def test_bank_packet_keeps_periods_separate_and_preserves_bank_disclosures():
    records=rows()
    records.append({'ticker':'TEST','kind':'profile','temporal':{'state':'current'},
                    'payload':{'industry':'Banks - Diversified'}})
    records[0]['period_end']='2026-06-30'
    records[0]['payload']['text']=('Company results. '*200+
        'Net income grew 13% excluding significant items. '
        'Tangible book value per share of $100.00, up 10%.')
    analysis={'fundamentals':{'net_income':21e9,'net_income_period':'2026-06-30',
        'cashflow_net_income':37e9,'cashflow_cfo':-200e9,'cashflow_unit':'USD',
        'cashflow_window':{'start':'2026-01-01','end':'2026-06-30','duration_class':'ytd'}},
        'valuation':{'equity_value_inputs':{'reference_price':300}}}
    context,_=packet('TEST',records,analysis)
    assert context['business_type']=='bank'
    assert context['fundamentals']['quarterly_income']['net_income']==21e9
    assert context['fundamentals']['year_to_date_income']['net_income']==37e9
    assert 'cashflow_cfo' not in context['fundamentals']['quarterly_income']
    assert '13% excluding significant items' in context['documents'][0]['text']
    assert context['valuation']['price_to_tangible_book']['ratio']==3
    assert context['valuation']['price_to_tangible_book']['source_id']=='S1'


def test_bank_selection_does_not_apply_to_nonbank_or_stale_profiles():
    records=rows()+[{'ticker':'TEST','kind':'profile','temporal':{'state':'current'},
                    'payload':{'industry':'Insurance - Life','sic':'6311'}}]
    assert packet('TEST',records,{})[0]['business_type']=='general'
    records[-1]['payload']={'sic':'6021'}
    records[-1]['temporal']['state']='context_only'
    assert packet('TEST',records,{})[0]['business_type']=='general'


def test_priority_passages_match_capitalized_terms():
    from advisor.investigator.reasoner import passages
    text='Boilerplate. '*1000+'Standardized CET1 capital ratio requirement was 11.5%. '+'Other text. '*1000
    excerpt=passages(text,3000,priority_terms=('Standardized CET1 capital ratio requirement',))
    assert 'requirement was 11.5%' in excerpt
