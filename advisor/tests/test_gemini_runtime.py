import json
import pytest
from advisor.investigator import gemini,runtime

SCHEMA={'type':'object','properties':{'answer':{'type':'string'}},'required':['answer'],'additionalProperties':False}
CONFIG={'GEMINI_API_KEY':'private-test-key'}


def test_search_feed_failure_is_not_reported_as_successful_empty_search(monkeypatch):
    def failed(*args,**kwargs):raise OSError('offline')
    monkeypatch.setattr(gemini.collectors,'fetch',failed)
    with pytest.raises(RuntimeError,match='Both public search feeds failed'):
        gemini.search('Nvidia earnings')


def response(value,finish='STOP',code=200):
    class Response:
        status_code=code
        def json(self):return {'candidates':[{'finishReason':finish,'content':{'parts':[{'text':json.dumps(value)}]}}],'usageMetadata':{'totalTokenCount':10}}
    return Response()


def test_gemini_transport_keeps_credentials_out_of_url_and_omits_paid_tools():
    def post(url,**kw):
        assert 'private-test-key' not in url
        assert kw['headers']['x-goog-api-key']=='private-test-key'
        assert 'tools' not in kw['json'] and kw['allow_redirects'] is False
        assert kw['json']['generationConfig']['responseJsonSchema']==SCHEMA
        return response({'answer':'ok'})
    value,usage=gemini.invoke('test',SCHEMA,config=CONFIG,post=post)
    assert value=={'answer':'ok'} and usage['provider']=='gemini'
    assert 'private-test-key' not in json.dumps(usage)


@pytest.mark.parametrize('finish,code',[('MAX_TOKENS',200),('STOP',429),('STOP',403)])
def test_failed_gemini_response_cannot_become_a_report(finish,code):
    with pytest.raises(RuntimeError):gemini.generate('test',SCHEMA,CONFIG,10,post=lambda *a,**k:response({'answer':'x'},finish,code))


def test_gemini_selection_and_status_do_not_require_openai(monkeypatch,tmp_path):
    p=tmp_path/'config';p.write_text('GEMINI_API_KEY=private-test-key\n')
    monkeypatch.setenv('ADVISOR_RESEARCH_ENV',str(p))
    for key in ('OPENAI_API_KEY','GEMINI_API_KEY','ADVISOR_RESEARCH_PROVIDER','ADVISOR_RESEARCH_MODEL'):monkeypatch.delenv(key,raising=False)
    assert runtime.status()=={'configured':True,'provider':'Gemini API','model':'gemini-3.8-flash'}
    assert runtime.require_config()['GEMINI_API_KEY']=='private-test-key'


def test_research_only_admits_fetched_urls_and_records_actual_search(monkeypatch):
    from advisor.investigator.reasoner import RESEARCH_SCHEMA
    calls=[]
    def post(url,**kw):
        calls.append(kw['json'])
        if len(calls)==1:return response({'queries':['actual query'],'source_urls':[]})
        return response({'sources':[{'url':u,'title':'Results','dimension':'fundamentals','published_at':'2026-09-01','event_at':'','date_excerpt':'September 1, 2026','excerpt':'Revenue increased materially.','why_material':'Growth'} for u in ['https://example.com/results','https://fabricated.example/']], 'relationships':[],'unresolved':[],'queries_run':['invented query']})
    monkeypatch.setattr(gemini.collectors,'public_url',lambda url:url)
    output,usage=gemini.invoke('test',RESEARCH_SCHEMA,config=CONFIG,web=True,post=post,
        searcher=lambda q:[{'url':'https://example.com/results','title':'Results'}],
        reader=lambda url:{'text':'September 1, 2026 Revenue increased materially.'})
    assert [x['url'] for x in output['sources']]==['https://example.com/results']
    assert usage['queries_observed']==['actual query']
    assert len(calls)==2 and all('tools' not in c for c in calls)


def test_explicit_service_failure_retries_once(monkeypatch):
    monkeypatch.setattr(gemini.time,'sleep',lambda _:None)
    calls=[]
    def post(*a,**k):
        calls.append(1)
        return response({'answer':'ok'},code=503 if len(calls)==1 else 200)
    assert gemini.generate('test',SCHEMA,CONFIG,30,post)[0]=={'answer':'ok'}
    assert len(calls)==2


def test_cli_defaults_to_full_research(monkeypatch,tmp_path):
    from advisor.investigator import __main__ as cli
    seen=[]
    class Process:
        exitcode=0
        def __init__(self,*,target,args):seen.append(args[0].scan_only)
        def start(self):pass
        def join(self,*args):pass
        def is_alive(self):return False
    class Context:pass
    ctx=Context();ctx.Process=Process
    monkeypatch.setattr(cli.multiprocessing,'get_context',lambda _:ctx)
    monkeypatch.setattr('sys.argv',['investigator','NVDA','--data-dir',str(tmp_path)])
    assert cli.main()==0 and seen==[False]
    monkeypatch.setattr('sys.argv',['investigator','NVDA','--data-dir',str(tmp_path),'--scan-only'])
    assert cli.main()==0 and seen==[False,True]


def test_reviewer_receives_computed_technical_evidence():
    from advisor.investigator.reasoner import review
    def runner(prompt,schema,**kwargs):
        assert '220.082' in prompt and 'COMPUTED RESULTS' in prompt
        return {},{}
    review('NVDA',{'insights':[]},[],runner=runner,analysis={'technicals':{'ma20':220.082}})
