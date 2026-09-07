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
