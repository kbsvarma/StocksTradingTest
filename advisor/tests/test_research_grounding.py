import pytest
from advisor.investigator.research_grounding import selection_schema,expand,known_clock
from advisor.investigator.reasoner import RESEARCH_SCHEMA
from advisor.investigator.runtime import validate


def test_research_selections_bind_exact_text_and_application_date():
    docs=[{'url':'https://example.com/results','title':'Results','text':'Cash collections increased with sales.','published_at':'2026-07-29'},
          {'url':'https://example.com/undated','text':'A rumor without a publication date.'}]
    schema,cards=selection_schema(RESEARCH_SCHEMA,docs)
    chosen={'source_id':'R01','passage_id':'P01','dimension':'accounting','why_material':'Tests collection quality.'}
    validate(chosen,schema['properties']['sources']['items'])
    packet=expand({'sources':[chosen]},cards)
    assert packet['sources'][0]['excerpt']==docs[0]['text']
    assert packet['sources'][0]['published_at'].startswith('2026-07-29')
    assert len(cards)==1 and packet['sources'][0]['event_at']==''
    with pytest.raises(ValueError):expand({'sources':[{**chosen,'source_id':'R02'}]},cards)


def test_sec_exhibit_clock_requires_same_verified_accession():
    known=[{'url':'https://www.sec.gov/Archives/edgar/data/123/00012326/filing.htm','published_at':'2026-07-29'}]
    assert known_clock('https://www.sec.gov/Archives/edgar/data/123/00012326/ex99.htm',known)=='2026-07-29'
    assert known_clock('https://www.sec.gov/Archives/edgar/data/123/00012327/ex99.htm',known) is None
    assert known_clock('https://sec.gov.example.com/Archives/edgar/data/123/00012326/ex99.htm',known) is None


def test_local_research_uses_required_queries_and_cannot_rewrite_citations(monkeypatch):
    from advisor.investigator import gemini
    monkeypatch.setattr(gemini.collectors,'public_url',lambda u:u)
    calls=[]
    def generate(prompt,schema,config,timeout,post=None,**kw):
        calls.append(schema)
        value={'queries':['model query'],'source_urls':[],'relationships':[]} if len(calls)==1 else {
            'sources':[{'source_id':'R01','passage_id':'P01','dimension':'accounting','why_material':'Collection timing.'}],
            'queries_run':['invented query'],'unresolved':[],'relationships':[]}
        validate(value,schema)
        return value,{'provider':'ollama','model':'fixture'}
    packet,usage=gemini.invoke('Investigate ACME',RESEARCH_SCHEMA,config={'ADVISOR_RESEARCH_PROVIDER':'ollama'},web=True,
        generate_fn=generate,research_queries=['business query'],searcher=lambda q:[{'url':'https://example.com/results'}],
        reader=lambda u:{'text':'Cash collections increased with sales.','published_at':'2026-07-29','title':'Results'})
    assert len(calls)==1 and usage['source_capture']=='application_extractive'
    assert usage['queries_observed']==['business query','model query']
    assert packet['sources'][0]['excerpt']=='Cash collections increased with sales.'
    assert packet['sources'][0]['url']=='https://example.com/results'


def test_default_investigator_entrypoint_forwards_research_context(monkeypatch):
    from advisor.investigator import reasoner,runtime
    seen=[]
    def transport(prompt,schema,**kw):
        seen.append(kw)
        return {'sources':[],'relationships':[],'unresolved':[],'queries_run':[]},{'queries_observed':['real query']}
    monkeypatch.setattr(runtime,'invoke',transport)
    analysis={'issuer_identity':{'name':'Acme'},'original_sources':[{'url':'https://example.com/results','published_at':'2026-07-29'}],'coverage':[]}
    packet,usage=reasoner.research('ACME',analysis)
    assert seen[0]['web'] and seen[0]['research_queries'][0].startswith('Acme')
    assert seen[0]['known_sources']==analysis['original_sources']
    assert packet['queries_run']==['real query']
