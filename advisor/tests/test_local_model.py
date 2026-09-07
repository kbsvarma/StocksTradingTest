import json
import pytest
from advisor.investigator import runtime, reasoner, ollama

SCHEMA=reasoner.obj({'answer':reasoner.S})


def test_local_provider_needs_no_key_and_stays_on_loopback(monkeypatch):
    monkeypatch.setenv('ADVISOR_RESEARCH_PROVIDER','ollama')
    assert runtime.status()['configured']
    def post(url,**kw):
        assert url=='http://127.0.0.1:11434/api/chat'
        assert 'headers' not in kw and kw['allow_redirects'] is False
        assert kw['json']['format']==SCHEMA and kw['json']['stream'] is True
        assert kw['json']['truncate'] is False and kw['json']['shift'] is False
        assert kw['json']['think'] is True
        assert 'OUTPUT SCHEMA GUIDE' in kw['json']['messages'][-1]['content']
        assert '"answer"' in kw['json']['messages'][-1]['content']
        class Response:
            status_code=200
            def iter_lines(self):yield json.dumps({'done':True,'done_reason':'stop','message':{'content':'{"answer":"local"}'},'eval_count':8}).encode()
            def close(self):pass
        return Response()
    result,usage=runtime.invoke('test',SCHEMA,post=post)
    assert result=={'answer':'local'} and usage['provider']=='ollama'
    assert usage['thinking_enabled'] is True


@pytest.mark.parametrize('packet',[
    {'done':True,'done_reason':'length','message':{'content':'{"answer":"truncated"}'}},
    {'done':True,'done_reason':'stop','message':{'content':'{"answer":7}'}},
    {'done':False,'message':{'content':'{"answer":"unfinished"}'}},
])
def test_local_invalid_output_is_not_a_report(packet):
    class Response:
        status_code=200
        def iter_lines(self):yield json.dumps(packet).encode()
        def close(self):pass
    with pytest.raises(RuntimeError):ollama.generate('test',SCHEMA,{},10,post=lambda *a,**k:Response())


def test_context_reduction_keeps_required_source_and_time_basis(monkeypatch):
    monkeypatch.setenv('ADVISOR_RESEARCH_PROVIDER','ollama')
    from advisor.investigator.temporal import record,annotate
    rows=annotate([record(ticker='NVDA',source='sec_facts',kind='fundamental',
        payload={'metric':'revenue','value':123,'unit':'USD','duration_class':'quarter'},published_at='2026-08-20',period_end='2026-07-31',retrieved_at='2026-09-07')],'2026-09-07')
    context=reasoner.evidence_context(rows,required_ids=[rows[0]['id']])
    assert context[0]['payload']==rows[0]['payload']
    assert context[0]['period_end']==rows[0]['period_end']
    assert context[0]['temporal']==rows[0]['temporal']
    analysis=json.loads(reasoner.analysis_context({'findings':[{'x':1}],'since_previous_investigation':['noise']*50000}))
    assert analysis['findings']==[{'x':1}]
    assert 'omitted' in analysis['context_scope']


def test_oversize_local_context_fails_before_network():
    with pytest.raises(ValueError,match='context exceeds'):
        ollama.generate('x'*53000,SCHEMA,{},10,post=lambda *a,**k:pytest.fail('network'))


def test_latest_filing_cannot_be_crowded_out_by_company_profile(monkeypatch):
    monkeypatch.setenv('ADVISOR_RESEARCH_PROVIDER','ollama')
    rows=[{'id':'profile','kind':'profile','temporal':{'state':'current'},'payload':{'longName':'Company','longBusinessSummary':'noise '*10000}},
          {'id':'filing','kind':'document','temporal':{'state':'current'},'payload':{'document_class':'periodic_filing','text':'Supply commitments increased materially.'}},
          {'id':'fact','kind':'fundamental','temporal':{'state':'current'},'payload':{'value':100,'metric':'revenue'}}]
    selected=reasoner.evidence_context(rows,limit=1500,required_ids=['fact'])
    assert selected[0]['id']=='filing'
    assert 'longBusinessSummary' not in json.dumps(selected)
    assert any(x['id']=='fact' for x in selected)


def test_closed_citation_schema_cannot_relabel_old_measurement_current():
    row={'id':'old','kind':'fundamental','temporal':{'state':'context_only'}}
    schema=reasoner.local_synthesis_schema([row])['properties']['insights']['items']['properties']['evidence']['items']
    runtime.validate({'source_id':'old','excerpt':'exact','use':'historical_comparison'},schema)
    with pytest.raises(ValueError):runtime.validate({'source_id':'old','excerpt':'exact','use':'current'},schema)
    with pytest.raises(ValueError):runtime.validate({'source_id':'invented','excerpt':'exact','use':'historical_comparison'},schema)


def test_structured_citation_cannot_invent_a_json_excerpt():
    row={'id':'current','kind':'fundamental','temporal':{'state':'current'},
         'payload':{'metric':'cash','value':22443000000},'exact_quote_examples':['"value": 22443000000']}
    schema=reasoner.local_synthesis_schema([row])['properties']['insights']['items']['properties']['evidence']['items']
    runtime.validate({'source_id':'current','excerpt':'"value": 22443000000','use':'current'},schema)
    with pytest.raises(ValueError):
        runtime.validate({'source_id':'current','excerpt':'"value": 22443000000, "metric": "cash"','use':'current'},schema)


def test_evidence_aliases_preserve_immutable_source_identity_and_time():
    row={'id':'immutablehash','ticker':'TEST','kind':'fundamental','temporal':{'state':'context_only'},
         'period_end':'2025-06-30','payload':{'metric':'cash','value':10,'unit':'USD'},'exact_quote_examples':['"value": 10']}
    aliases,body,calcs,mapping=reasoner.local_packet([row],{})
    assert aliases[0]['id']=='E01' and '2025-06-30' in body and 'context_only' in body
    proposal={'insights':[{'evidence':[{'source_id':'E01','excerpt':'"value": 10','use':'historical_comparison'}]}]}
    expanded=reasoner.translate_citations(proposal,{v:k for k,v in mapping.items()})
    assert expanded['insights'][0]['evidence'][0]['source_id']=='immutablehash'
    assert proposal['insights'][0]['evidence'][0]['source_id']=='E01'
    schema=reasoner.local_synthesis_schema(aliases)['properties']['insights']['items']['properties']['evidence']['items']
    with pytest.raises(ValueError):runtime.validate({'source_id':'E01','excerpt':'"value": 10','use':'current'},schema)
    with pytest.raises(ValueError):reasoner.translate_citations(proposal,{})


def test_saved_sampling_profile_reaches_local_request(monkeypatch,tmp_path):
    settings=tmp_path/'research.env'
    settings.write_text('ADVISOR_RESEARCH_PROVIDER=ollama\nADVISOR_RESEARCH_SAMPLING=conservative\n')
    monkeypatch.setenv('ADVISOR_RESEARCH_ENV',str(settings))
    monkeypatch.delenv('ADVISOR_RESEARCH_SAMPLING',raising=False)
    def post(url,**kw):
        assert kw['json']['options']['temperature']==.2
        assert kw['json']['options']['presence_penalty']==0
        class Response:
            status_code=200
            def iter_lines(self):yield json.dumps({'done':True,'done_reason':'stop','message':{'content':'{"answer":"checked"}'}}).encode()
            def close(self):pass
        return Response()
    result,usage=ollama.generate('test',SCHEMA,runtime.config(),10,post=post)
    assert result['answer']=='checked'
    assert usage['sampling']['temperature']==.2
