from datetime import datetime,timezone,timedelta
import pytest
from advisor.investigator.search import resolve,AmbiguousCompany
from advisor.investigator.presentation import decision_brief
from advisor.investigator.temporal import record

NOW=datetime(2026,9,6,21,tzinfo=timezone.utc)

@pytest.mark.parametrize('name',['Nividia','Nvidia','Nvda',' NVDIA ','nvidia'])
def test_nvidia_variants_resolve_without_network(name):
    assert resolve(name,search=lambda _:pytest.fail('Unnecessary lookup'))=='NVDA'

def test_ambiguous_company_requires_a_listing_choice():
    results=[{'symbol':'AAA','shortname':'Acme US','quoteType':'EQUITY'},{'symbol':'BBB','shortname':'Acme UK','quoteType':'EQUITY'}]
    with pytest.raises(AmbiguousCompany) as exc:resolve('Acme',search=lambda _:results)
    assert [r['ticker'] for r in exc.value.choices]==['AAA','BBB']

def test_unknown_search_cannot_become_a_report():
    with pytest.raises(ValueError,match='No stock found'):resolve('No Such Company',search=lambda _:[])

def test_provider_search_resolves_unique_name_and_exact_ticker():
    assert resolve('Acme Inc',search=lambda _:[{'symbol':'ACME','quoteType':'EQUITY','shortname':'Acme Inc'}])=='ACME'
    assert resolve('ACME',search=lambda _:[{'symbol':'OTHER','quoteType':'EQUITY'},{'symbol':'ACME','quoteType':'EQUITY'}])=='ACME'

def report(rows,findings=()):
    return {'ticker':'TEST','evidence':rows,'analysis':{'findings':list(findings)},'collection':{}}

def test_macro_records_cannot_verify_a_company_or_generate_conditions():
    r=record(ticker='TEST',source='fred',kind='macro',payload={'value':5},retrieved_at=NOW,observed_at=NOW)
    b=decision_brief(report([r]),NOW)
    assert b['verdict']=='SYMBOL NOT VERIFIED' and not b['conditions']

def test_current_growth_and_cash_risk_yield_mixed_assessment_then_expire():
    r=record(ticker='TEST',source='sec_facts',kind='fundamental',payload={'value':10,'metric':'revenue','unit':'USD','duration_class':'quarter'},retrieved_at=NOW,published_at=NOW,period_end='2026-06-30')
    findings=[{'id':'revenue_growth','title':'Revenue growth','direction':'bullish','evidence_ids':[r['id']]},
              {'id':'cash_conversion','title':'Weak cash conversion','direction':'bearish','evidence_ids':[r['id']]}]
    raw=report([r],findings)
    b=decision_brief(raw,NOW)
    assert b['verdict'].startswith('MIXED') and 'Growth and earnings quality' in b['drivers'][0]
    expired=decision_brief(raw,NOW+timedelta(days=400))
    assert not expired['findings'] and not expired['conditions']
    assert len(expired['historical'])==2

def test_future_evidence_cannot_enter_a_current_brief():
    r=record(ticker='TEST',source='sec_facts',kind='fundamental',payload={'value':10,'metric':'revenue','unit':'USD','duration_class':'quarter'},retrieved_at=NOW,published_at=NOW+timedelta(days=1),period_end='2026-06-30')
    f={'id':'revenue_growth','title':'Growth','direction':'bullish','evidence_ids':[r['id']]}
    assert not decision_brief(report([r],[f]),NOW)['findings']
