import pytest
from advisor.investigator.discovery import relevant


def test_relevance_rejects_generic_quotes_and_unrelated_search_results():
    query='NVIDIA customer payment terms receivables 2026'
    assert not relevant(query,{'url':'https://finance.yahoo.com/quote/NVDA/','title':'NVIDIA customer payment terms'})
    assert not relevant(query,{'url':'https://example.com/cafeteria','title':'Cafeteria restaurant in Santo Domingo'})
    assert not relevant(query,{'url':'https://nvidia.com/en-us/','title':'World Leader NVIDIA'})
    assert relevant(query,{'url':'https://example.com/nvidia-payment-terms','title':'NVIDIA extends customer terms','snippet':'Receivables increased.'})


def test_no_relevant_search_results_is_a_failure_not_claimed_coverage(monkeypatch):
    import sys,types
    monkeypatch.setitem(sys.modules,'ddgs',types.SimpleNamespace(DDGS=lambda **kw:None))
    from advisor.investigator.discovery import search
    class Client:
        def text(self,*a,**kw):return [{'href':'https://example.com/cafe','title':'Restaurants in Santo Domingo','body':'Lunch'}]
    with pytest.raises(RuntimeError,match='no relevant'):search('NVIDIA payment terms',client=Client())


def test_first_round_prioritizes_business_driver_over_optional_coverage():
    from advisor.investigator.planner import priority_queries
    a={'issuer_identity':{'name':'Acme Corporation'},'fundamentals':{'revenue_period':'2026-06-30'},'findings':[{'id':'earnings_normalization'},{'id':'cash_conversion'}]}
    queries=priority_queries(a,'ACME')
    assert len(queries)==2 and all(q.startswith('Acme Corporation') for q in queries)
    assert 'guidance' in queries[0] and 'investment gains' in queries[1]
    assert '2026' in queries[1] and '2027' not in queries[1]
