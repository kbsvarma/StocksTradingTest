import json
import pytest
from advisor.investigator import runtime
from advisor.investigator.quarters import derive
from advisor.investigator.temporal import record

SCHEMA={'type':'object','properties':{'answer':{'type':'string'}},'required':['answer'],'additionalProperties':False}

@pytest.fixture(autouse=True)
def isolate(monkeypatch,tmp_path):
    monkeypatch.setenv('ADVISOR_RESEARCH_ENV',str(tmp_path/'missing'))
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)


def test_missing_application_key_never_calls_transport():
    with pytest.raises(RuntimeError,match='No research model connected'):
        runtime.invoke('test',SCHEMA,post=lambda *a,**k:pytest.fail('No credential'))


def response(output,status='completed',code=200):
    class Response:
        status_code=code
        def json(self):return {'status':status,'output':output,'usage':{'output_tokens':12}}
    return Response()


def message(value):return {'type':'message','content':[{'type':'output_text','text':json.dumps(value)}]}


def test_real_tool_trace_is_separate_from_model_text(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','test-secret')
    def post(url,**kw):
        assert kw['allow_redirects'] is False
        assert kw['json']['store'] is False
        assert kw['json']['text']['format']['strict'] is True
        assert kw['json']['tool_choice']=='required'
        return response([{'type':'web_search_call','action':{'type':'search','queries':['actual query'],'sources':[{'url':'https://example.com'}]}},message({'answer':'claimed query'})])
    result,usage=runtime.invoke('test',SCHEMA,web=True,post=post)
    assert usage['queries_observed']==['actual query']
    assert 'test-secret' not in json.dumps(usage)
    assert result['answer']=='claimed query'


@pytest.mark.parametrize('output,status,web',[
    ([message({'answer':3})],'completed',False),
    ([message({'answer':'x'})],'incomplete',False),
    ([message({'answer':'x'})],'completed',True),
    ([{'type':'message','content':[{'type':'refusal'}]}],'completed',False),
])
def test_invalid_or_unfinished_result_is_not_published(monkeypatch,output,status,web):
    monkeypatch.setenv('OPENAI_API_KEY','test-secret')
    with pytest.raises(RuntimeError):runtime.invoke('test',SCHEMA,web=web,post=lambda *a,**k:response(output,status))


def test_synthesis_has_no_web_tools_and_provider_error_is_sanitized(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','test-secret')
    def post(url,**kw):
        assert 'tools' not in kw['json']
        return response([],code=401)
    with pytest.raises(RuntimeError,match='credential rejected') as exc:runtime.invoke('test',SCHEMA,post=post)
    assert 'test-secret' not in str(exc.value)


def flow(end,value,duration,published='2026-08-01',metric='revenue',unit='USD'):
    return record(ticker='MSFT',source='sec_facts',kind='fundamental',
        payload={'metric':metric,'unit':unit,'value':value,'duration_class':duration,'tag':metric},
        retrieved_at='2026-09-01',published_at=published,period_start='2025-07-01',period_end=end)


def test_q4_uses_matching_ytd_and_preserves_both_inputs():
    annual=flow('2026-06-30',300,'annual');ytd=flow('2026-03-31',210,'ytd')
    result=derive([annual,ytd],'2026-09-07')
    quarter=result[-1]
    assert len(result)==3 and quarter['payload']['value']==90
    assert quarter['period_start']=='2026-04-01'
    assert quarter['payload']['input_ids']==[annual['id'],ytd['id']]
    assert len(derive(result,'2026-09-07'))==3


@pytest.mark.parametrize('metric,unit,published',[('eps','USD','2026-08-01'),('revenue','EUR','2026-08-01'),('revenue','USD','2026-10-01')])
def test_q4_rejects_nonadditive_mismatched_and_future_inputs(metric,unit,published):
    rows=[flow('2026-06-30',300,'annual',metric=metric),flow('2026-03-31',210,'ytd',metric=metric,unit=unit,published=published)]
    assert len(derive(rows,'2026-09-07'))==2
