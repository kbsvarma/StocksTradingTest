import pytest
from bs4 import BeautifulSoup
from advisor.investigator.markdown_research import to_markdown,chunks,retrieve,TOPICS
from advisor.investigator.brief import link_sources,request_model,markdown_packet


def test_grouped_citations_keep_all_sources_and_reject_invented_ones():
    docs=[{'source_id':'S1','url':'https://example.com/release'},{'source_id':'S2','url':'https://example.com/filing'}]
    text,links=link_sources('Cash generation [S1, S2].',docs)
    assert len(links)==2 and '[Source 2]' in text
    with pytest.raises(ValueError,match='Unknown'):link_sources('Claim [S1, S99]',docs)
    with pytest.raises(ValueError,match='citations'):link_sources('Claim [S1, S2] https://invented.example/data',docs)


def test_markdown_retains_period_headers_and_repeats_them_when_chunking():
    html='<h2>Cash flow in millions</h2><table><tr><th>Measure</th><th>Quarter Aug 2</th><th>Nine months Aug 2</th></tr>'
    html+=''.join(f'<tr><td>Operating cash flow line {i}</td><td>14197</td><td>32950</td></tr>' for i in range(35))+'</table>'
    md=to_markdown(BeautifulSoup(html,'html.parser'))
    sections=[s for s in chunks(md,1000) if '32950' in s]
    assert len(sections)>1
    assert all('Quarter Aug 2' in s and 'Nine months Aug 2' in s for s in sections)
    assert '14197' in md


def test_retrieval_reserves_space_for_each_topic_instead_of_truncating_prefix():
    docs=[{'source_id':'S1','text':'\n\n'.join(' '.join([q]*7)+'.' for q in TOPICS.values())}]
    selected,coverage=retrieve(docs,budget=23000)
    assert all(isinstance(v,list) and v for v in coverage.values())
    assert sum(len(s['text']) for s in selected)<=23000


def test_retry_is_bounded_and_includes_rate_limit():
    statuses=iter([429,200]);calls=[]
    class Response:
        def __init__(self,status):self.status_code=status
    def post(*args,**kwargs):calls.append(1);return Response(next(statuses))
    assert request_model('test',{}, {'GEMINI_API_KEY':'secret'},post=post).status_code==200
    assert len(calls)==2
    with pytest.raises(RuntimeError,match='429'):
        request_model('test',{}, {'GEMINI_API_KEY':'secret'},post=lambda *a,**kw:Response(429))


def test_section_citations_require_an_actually_supplied_section():
    docs=[{'source_id':'S1','url':'https://example.com/release'}]
    sections=[{'source_id':'S1','section_id':'S1C17'}]
    text,links=link_sources('Segment revenue [S1C17, S1].',docs,sections)
    assert links=={'https://example.com/release'}
    with pytest.raises(ValueError,match='Unknown'):link_sources('Invented [S1C99]',docs,sections)


def test_release_headlines_survive_keyword_dense_boilerplate():
    text='AI revenue grew 221 percent. Semiconductor segment revenue grew 127 percent.\n\n'+('Uncertainties that could affect growth demand revenue segment software customers. '*30)
    sections,_=retrieve([{'source_id':'S1','document_class':'earnings_release','published_at':'2026-09-02','text':text}])
    assert any('221 percent' in s['text'] for s in sections)
from advisor.investigator.brief import verify_report

def test_review_keeps_draft_evidence_and_adds_counterevidence():
    docs=[{'source_id':'S1','url':'https://example.com/release'}]
    context={'documents':docs,'retrieved_sections':[{'source_id':'S1','section_id':'S1C900','text':'Specific original observation retained for checking.'}]}
    rows=[{'url':docs[0]['url'],'kind':'document','payload':{'text':('Customer financing guarantees credit default risk. '*12)}}]
    report='Decision: HOLD\nSummary: The evidence supports a measured holding decision.\n'+('Evidence and interpretation with explicit limits. '*12)+'[S1C900]'
    class Response:
        status_code=200
        def json(self):return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':report}]}}]}
    def post(*args,**kwargs):
        prompt=kwargs['json']['contents'][0]['parts'][0]['text']
        assert 'Specific original observation' in prompt
        assert 'Customer financing guarantees' in prompt
        return Response()
    value,usage=verify_report({'report_markdown':'Original draft'},context,rows,{'GEMINI_API_KEY':'secret'},post=post)
    assert usage['state']=='complete'
    assert 'Source 1C900' in value['report_markdown']


def test_compensation_expense_is_not_selected_from_deferred_tax_asset_table():
    docs=[{'source_id':'S1','text':'| June 30 | 2026 | 2025 |\n| Deferred Income Tax Assets | | |\n| Stock-based compensation expense | 945 | 909 |\n\n| Three months ended June 30 | 2026 | 2025 |\n| Adjustments to reconcile net income to net cash from operations | | |\n| Stock-based compensation expense | 3122 | 3073 |'}]
    selected,coverage=retrieve(docs)
    quality=[s['text'] for s in selected if s['section_id'] in coverage['quality']]
    assert any('3122' in s for s in quality)
    assert not any('945' in s for s in quality)


def test_unvalidated_trailing_ratio_and_ambiguous_balance_comparison_not_published():
    from advisor.investigator.brief import scope_guard
    text='Cash was $23.98 billion, up from $16.18 billion in the prior quarter [S1].\n\nUsing three quarters as a proxy, TTM EPS is $13.32 and P/E is 26.9x.\n\nAI revenue was $16.7 billion [S1].'
    result=scope_guard(text)
    assert '16.18' not in result and '26.9' not in result
    assert '$23.98 billion' in result and '$16.7 billion' in result
    assert 'not established' in result
