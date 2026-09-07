"""Failure-oriented tests for clocks, accounting comparisons, research and publication."""
import copy
import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
import pytest
from advisor.investigator.temporal import record,annotate,assess
from advisor.investigator.analysis import fundamental_metrics,technicals,deduplicate_news,reverse_dcf,analyze
from advisor.investigator.reasoner import validate_synthesis,finalize,ingest_web
from advisor.investigator.engine import run,load_latest
from advisor.investigator.collectors import symbol,public_url
from advisor.investigator.catalog import SOURCES,DIMENSIONS

NOW=datetime(2026,9,6,21,tzinfo=timezone.utc)

def fact(metric,value,start,end,*,published='2026-08-26T20:00:00Z',unit='USD'):
    return record(ticker='TEST',source='sec_facts',kind='fundamental',payload={'metric':metric,'value':value,'unit':unit,'duration_class':'quarter' if start else 'instant'},
                  period_start=start,period_end=end,published_at=published,retrieved_at=NOW,url='https://www.sec.gov/test',authority='primary')


def doc(text='Revenue increased 20 percent in the latest fiscal quarter.',**kw):
    return record(ticker='TEST',source='issuer_ir',kind='document',payload={'text':text},url='https://example.com/release',title='Results',
                  published_at=kw.pop('published_at','2026-09-04T12:00:00Z'),retrieved_at=NOW,**kw)


def proposal(r):
    return {'summary':'Improving demand','action':'wait_for_trigger','action_reason':'Demand improved; wait for price confirmation.',
            'entry_conditions':['Confirm demand and price support'],'exit_conditions':['Demand reverses'],'next_checks':[],'contradictions':[],
            'insights':[{'id':'one','title':'Demand improvement','direction':'bullish','what_changed':'Revenue grew',
                         'mechanism':'Higher utilization improves earnings','what_is_priced_in':'Unknown; test valuation','counterargument':'Working capital may absorb growth',
                         'invalidation':'Growth reverses','horizon':'Next quarter','materiality':3,
                         'evidence':[{'source_id':r['id'],'excerpt':r['payload']['text'],'use':'current'}]}]}


def test_catalog_covers_every_dimension_without_duplicate_sources():
    assert len(SOURCES)>=40 and len({s.id for s in SOURCES})==len(SOURCES)
    assert set(DIMENSIONS)=={s.dimension for s in SOURCES}


def test_margin_level_is_distinct_from_percentage_point_change():
    rows=[fact('revenue',120,'2026-04-01','2026-06-30'),fact('net_income',36,'2026-04-01','2026-06-30'),
          fact('revenue',100,'2025-04-01','2025-06-30'),fact('net_income',20,'2025-04-01','2025-06-30')]
    result,citations=fundamental_metrics(annotate(rows,NOW))
    assert result['net_income_margin_pct']==30
    assert result['net_income_prior_margin_pct']==20
    assert result['net_income_margin_change_pp']==10
    assert len(citations['net_income_margin_change_pp'])==4


def test_small_estimate_changes_do_not_become_primary_thesis_drivers():
    row=record(ticker='TEST',source='yahoo_estimates',kind='estimate',
        payload={'dataset':'eps_trend','rows':[{'period':'0y','current':10.02,'30daysAgo':10}]},retrieved_at=NOW,observed_at=NOW)
    result=analyze(annotate([row],NOW),'TEST')
    finding=next(x for x in result['findings'] if x['id']=='estimate_revision')
    assert finding['materiality']==1 and finding['direction']=='neutral'
    from advisor.investigator.grounding import decision_findings
    assert finding not in decision_findings(result)


@pytest.mark.parametrize('bad',['../../secrets','NVDA;cat','NVDA INT','https://localhost','A'*30,''])
def test_symbol_rejects_path_and_command_injection(bad):
    with pytest.raises(ValueError):symbol(bad)


def test_private_evidence_urls_are_rejected():
    with pytest.raises(ValueError):public_url('https://127.0.0.1/private')
    with pytest.raises(ValueError):public_url('http://example.com')


def test_republication_cannot_make_an_old_quarter_current():
    r=fact('revenue',100,'2025-01-01','2025-03-31',published='2026-09-06T10:00:00Z')
    assert 'old_measurement_period' in assess(r,NOW)['reasons']


def test_latest_quarterly_filing_is_not_expired_like_a_news_article():
    latest=record(ticker='TEST',source='sec_exhibits',kind='document',
        payload={'text':'Quarterly filing text','document_class':'periodic_filing','form':'10-Q'},
        period_end='2026-06-30',published_at='2026-07-20',retrieved_at=NOW)
    assert assess(latest,NOW)['state']=='current'
    old=copy.deepcopy(latest);old['period_end']='2025-06-30';old['published_at']=NOW.isoformat()
    assert 'old_measurement_period' in assess(old,NOW)['reasons']
    older=copy.deepcopy(latest);older['period_end']='2026-03-31'
    older['payload']['form']='10-K'
    # A newer periodic filing supersedes an otherwise recent financial period.
    older['period_end']='2026-05-31'
    assert annotate([older,latest],NOW)[0]['temporal']['reasons']==['superseded_measurement_period']
    missing=copy.deepcopy(latest);missing['period_end']=None
    assert 'measurement_period_unknown' in assess(missing,NOW)['reasons']
    article=doc(published_at='2026-07-20')
    assert assess(article,NOW)['state']=='context_only'


def test_newer_filing_does_not_replace_latest_measurement():
    a=fact('revenue',100,'2026-01-01','2026-03-31',published='2026-09-05T10:00:00Z')
    b=fact('revenue',120,'2026-04-01','2026-06-30')
    result,_=fundamental_metrics(annotate([a,b],NOW))
    assert result['revenue']==120


def test_ytd_cannot_be_used_as_quarter_comparison():
    r=fact('revenue',120,'2026-04-01','2026-06-30')
    annual=fact('revenue',400,'2025-01-01','2025-12-31');annual['payload']['duration_class']='annual'
    old=fact('revenue',100,'2025-04-01','2025-06-30')
    result,_=fundamental_metrics(annotate([r,annual,old],NOW))
    assert result['revenue_yoy_pct']==pytest.approx(20)


def test_cash_conversion_requires_exact_period_not_latest_available_income():
    cfo=fact('cfo',100,'2026-01-01','2026-06-30');cfo['payload']['duration_class']='ytd'
    income=fact('net_income',50,'2026-04-01','2026-06-30')
    result,_=fundamental_metrics(annotate([cfo,income],NOW))
    assert 'cash_conversion' not in result


@pytest.mark.parametrize('field',['published_at','retrieved_at','observed_at'])
def test_future_information_cannot_enter_current_report(field):
    r=doc();r[field]=(NOW+timedelta(minutes=1)).isoformat()
    assert assess(r,NOW)['state']=='excluded'


def test_recirculated_old_event_and_unknown_publication_are_context_only():
    r=doc(event_at='2026-01-01T00:00:00Z')
    assert 'old_event_recirculated' in assess(r,NOW)['reasons']
    r['published_at']=None
    assert assess(r,NOW)['state']=='context_only'


def test_short_interest_ages_from_settlement_not_download():
    r=record(ticker='TEST',source='exchange_short',kind='short_interest',payload={},period_end='2026-07-15',observed_at=NOW,retrieved_at=NOW)
    assert assess(r,NOW)['state']=='context_only'


def test_syndicated_headlines_are_one_cluster():
    def n(source):return record(ticker='TEST',source=source,kind='news',payload={'article_url':'https://example.com/same'},title='Company reports record earnings',published_at=NOW,retrieved_at=NOW)
    groups=deduplicate_news(annotate([n('a'),n('b')],NOW))
    assert len(groups)==1 and len(groups[0]['source_ids'])==2


def test_technical_flat_series_rsi_is_neutral_and_200_requires_history():
    r={'payload':{'bars':[{'Date':str(i),'Close':100,'High':101,'Low':99,'Volume':1000} for i in range(50)]}}
    t=technicals(r)
    assert t['rsi14']==50 and 'ma200' not in t and t['rv20_pct']==0


def test_reverse_expectations_are_monotonic_and_reject_invalid_discount():
    assert reverse_dcf(200,5)['required_growth_pct']>reverse_dcf(100,5)['required_growth_pct']
    with pytest.raises(ValueError):reverse_dcf(100,5,discount=.02)


def test_bound_excerpt_does_not_bypass_adversarial_review():
    r=annotate([doc()],NOW)[0];p=proposal(r)
    assert len(validate_synthesis(p,[r])[0])==1
    result=finalize(p,{'insights':[{'id':'one','supported':False,'reason':'Causal inference not supported'}],'action_supported':True},[r])
    assert not result['insights'] and result['action']=='investigate_further'


def test_unknown_or_stale_source_cannot_support_current_action():
    r=annotate([doc(published_at='2026-01-01T00:00:00Z')],NOW)[0];p=proposal(r)
    assert not validate_synthesis(p,[r])[0]
    p['insights'][0]['evidence'][0]['source_id']='fabricated'
    assert 'unknown_source' in validate_synthesis(p,[r])[1][0]['reasons']


def test_web_discovery_requires_actual_body_and_publication_date():
    packet={'sources':[{'url':'https://example.com/release','title':'Release','dimension':'guidance','published_at':'2026-09-04',
                        'event_at':'','date_excerpt':'September 4, 2026','excerpt':'Revenue increased 20 percent in the latest fiscal quarter.'}]}
    fetched=lambda url:{'text':'September 4, 2026 Revenue increased 20 percent in the latest fiscal quarter.','published_at':None}
    assert len(ingest_web('TEST',packet,fetcher=fetched)[0])==1
    packet['sources'][0]['excerpt']='Invented report of a transformative new contract'
    assert not ingest_web('TEST',packet,fetcher=fetched)[0]


def test_engine_partial_collection_preserves_report_and_detects_tampering(tmp_path):
    from advisor.investigator.temporal import utcnow
    now=utcnow()
    r=record(ticker='TEST',source='issuer_ir',kind='document',payload={'text':'Test statement with a clear economic observation.'},retrieved_at=now,published_at=now)
    def fail():raise TimeoutError()
    report=run('TEST',tmp_path,deep=False,collect={'ok':lambda:([r],[]),'bad':fail})
    assert report['collection']['bad']['status']=='failed' and len(report['evidence'])==1
    assert load_latest(tmp_path,'TEST')['snapshot_hash']==report['snapshot_hash']
    root=tmp_path/'intelligence'/'investigations'/'TEST'/report['run_id']
    raw=json.loads((root/'report.json').read_text());raw['ticker']='FAKE';(root/'report.json').write_text(json.dumps(raw))
    with pytest.raises(ValueError):load_latest(tmp_path,'TEST')


def test_headline_only_is_not_a_load_bearing_premise():
    r=record(ticker='TEST',source='yahoo_news',kind='news',payload={'headline':'Earnings surge','body_verified':False},published_at=NOW,retrieved_at=NOW)
    assert 'headline_discovery_only' in assess(r,NOW)['reasons']


def test_naive_provider_dates_remain_date_precision_not_machine_local_time():
    import pandas as pd
    from advisor.investigator.collectors import clean
    assert clean(pd.Timestamp('2026-09-04'))=='2026-09-04T23:59:59+00:00'


def test_trailing_cashflow_uses_annual_plus_ytd_minus_prior_ytd():
    from advisor.investigator.valuation import trailing_metric
    annual=fact('cfo',100,'2025-01-01','2025-12-31');annual['payload']['duration_class']='annual'
    ytd=fact('cfo',80,'2026-01-01','2026-06-30');ytd['payload']['duration_class']='ytd'
    prior=fact('cfo',50,'2025-01-01','2025-06-30');prior['payload']['duration_class']='ytd'
    r=trailing_metric(annotate([annual,ytd,prior],NOW),'cfo')
    assert r['value']==130 and len(r['ids'])==3
    assert trailing_metric(annotate([annual,ytd],NOW),'cfo') is None


def test_financial_company_does_not_get_generic_reverse_dcf():
    from advisor.investigator.valuation import valuation
    r=record(ticker='TEST',source='yahoo_profile',kind='profile',payload={'industry':'Banks - Regional'},observed_at=NOW,retrieved_at=NOW)
    result=valuation(annotate([r],NOW),'TEST')
    assert 'sector_method' in result and 'implied_growth_sensitivity' not in result


def test_deep_pipeline_publishes_only_challenged_bound_insights(tmp_path,monkeypatch):
    from advisor.investigator import reasoner
    from advisor.investigator.temporal import utcnow
    now=utcnow()
    r=record(ticker='TEST',source='issuer_ir',kind='document',payload={'text':'Revenue increased 20 percent in the latest fiscal quarter.'},url='https://example.com/release',published_at=now,retrieved_at=now)
    calls=[]
    def model(prompt,schema,**kwargs):
        calls.append(kwargs.get('web',False))
        if 'sources' in schema['properties']:
            return {'sources':[],'relationships':[],'unresolved':['Verify customer demand'],'queries_run':['invented query']},{'queries_observed':['TEST primary earnings release']}
        if 'summary' in schema['properties']:return proposal(r),{}
        return {'insights':[{'id':'one','supported':True,'reason':'Premises match'}],'action_supported':True,'action_reason':'Conditional only','missed_questions':[]},{}
    report=run('TEST',tmp_path,deep=True,collect={'fixture':lambda:([r],[])},model=model)
    assert calls==[True,True,False,False]
    assert report['synthesis']['action']=='wait_for_trigger' and len(report['synthesis']['insights'])==1
    assert report['search']['queries_run']==['TEST primary earnings release']
    assert 'invented query' not in report['search']['queries_run']


def test_source_correct_quote_still_rejected_when_claim_review_missing():
    r=annotate([doc()],NOW)[0]
    result=finalize(proposal(r),{'insights':[],'action_supported':True},[r])
    assert result['action']=='investigate_further'


def test_review_of_different_insights_cannot_approve_report():
    r=annotate([doc()],NOW)[0]
    reviewed={'insights':[{'id':'one','supported':True,'reason':'Something else'},
                           {'id':'invented','supported':True,'reason':'Unrequested claim'}],
              'action_supported':True}
    result=finalize(proposal(r),reviewed,[r])
    assert result['action']=='investigate_further' and not result['insights']
    assert 'review_does_not_match_proposal' in result['rejected_insights'][0]['reasons']


def test_bank_cash_movements_do_not_trigger_industrial_cash_quality_warning():
    profile=record(ticker='TEST',source='sec_filings',kind='profile',payload={'sic':'6021'},observed_at=NOW,retrieved_at=NOW)
    rows=[profile]
    for metric,value in [('cfo',-80),('net_income',10)]:
        rows.append(record(ticker='TEST',source='sec_facts',kind='fundamental',
            payload={'metric':metric,'value':value,'unit':'USD','duration_class':'ytd'},
            period_start='2026-01-01',period_end='2026-06-30',published_at='2026-07-20',retrieved_at=NOW))
    result=analyze(annotate(rows,NOW),'TEST')
    assert 'cash_conversion' not in result['fundamentals']
    assert not any(f['id']=='cash_conversion' for f in result['findings'])
    assert result['valuation']['cash_flow_model']=='not_applicable_financial_institution'
    assert rows[1]['payload']['value']==-80


def test_small_inventory_balance_is_scaled_before_ranking_risk():
    rows=[fact('revenue',100,'2026-04-01','2026-06-30'),fact('revenue',90,'2025-04-01','2025-06-30'),
          fact('inventory',2,None,'2026-06-30'),fact('inventory',1,None,'2025-06-30')]
    result=analyze(annotate(rows,NOW),'TEST')
    item=next(f for f in result['findings'] if f['id']=='inventory_divergence')
    assert item['materiality']==1 and item['direction']=='neutral'
    assert '2.0% of quarterly revenue' in item['detail']


def test_profit_surge_does_not_imply_operating_growth_without_reconciliation():
    rows=[]
    for metric,new,old in [('net_income',120,30),('op_income',40,32)]:
        rows.extend([fact(metric,new,'2026-04-01','2026-06-30'),fact(metric,old,'2025-04-01','2025-06-30')])
    result=analyze(annotate(rows,NOW),'TEST')
    item=next(f for f in result['findings'] if f['id']=='earnings_normalization')
    assert item['direction']=='neutral' and item['materiality']==3
    assert '300.0%' in item['detail'] and '25.0%' in item['detail']
    assert len(item['evidence_ids'])==4


def test_ticker_command_can_open_uncovered_investigation():
    from advisor.intelligence.terminal_data import parse_command
    assert parse_command('NVDA INT',{'TEST'})==('INT','NVDA',None)
    assert parse_command('NVDA',{'TEST'})==('INT','NVDA',None)
    assert parse_command('../../etc INT',{'TEST'})[2]


def test_retrieval_refresh_is_not_a_new_economic_event():
    from advisor.investigator.changes import compare
    r=annotate([doc()],NOW)[0];new=copy.deepcopy(r);new['retrieved_at']=(NOW+timedelta(hours=1)).isoformat();new['id']='new-download'
    assert compare([new],{'as_of':NOW.isoformat(),'evidence':[r]})['changed']==[]


def test_report_handoff_is_idempotent_and_preserves_original_clock(tmp_path):
    from advisor.investigator.handoff import queue_report
    from advisor.intelligence.access import Principal
    from advisor.intelligence.store import CallStore
    report=run('TEST',tmp_path,deep=False,collect={'empty':lambda:([],[])})
    principal=Principal('analyst','model','analyst')
    ident,created=queue_report(report,tmp_path,principal)
    assert created and queue_report(report,tmp_path,principal)==(ident,False)
    with CallStore(tmp_path/'intelligence'/'calls.sqlite',principal,readonly=True) as store:
        call=store.latest(ident)
        assert call['status']=='candidate' and call['action']=='watch'
        assert report['as_of'] in call['thesis']['what_changed']
        assert call['publication_lineage']['investigation']['snapshot_hash']==report['snapshot_hash']


def test_document_publication_ignores_dates_of_related_articles(monkeypatch):
    from advisor.investigator import collectors
    page=b'<html><meta property="article:published_time" content="2026-09-04T12:00:00Z"><body>Current article<time datetime="2025-01-01T00:00:00Z">Related article</time></body></html>'
    monkeypatch.setattr(collectors,'fetch',lambda url:page)
    assert collectors.document('https://example.com')['published_at']=='2026-09-04T12:00:00+00:00'


def test_filing_reader_removes_hidden_taxonomy_but_keeps_visible_disclosures(monkeypatch):
    from advisor.investigator import collectors
    page=b'<html><ix:header><ix:hidden>Taxonomy noise</ix:hidden></ix:header><div style="display: none">Hidden context</div><p>Supply commitments were $279 billion.</p></html>'
    monkeypatch.setattr(collectors,'fetch',lambda url:page)
    assert collectors.document('https://example.com')['text']=='Supply commitments were $279 billion.'


def test_compact_passages_cover_different_material_disclosures():
    from advisor.investigator.reasoner import passages
    text='Introduction. '+('filler '*200)+'Extended payment terms affect cash collection. '+('filler '*200)+'Guarantees create contingent exposure. '+('filler '*200)+'Supply commitments increased. '+('filler '*200)
    selected=passages(text,2500)
    assert all(x in selected for x in ('Extended payment terms','Guarantees create','Supply commitments'))
    assert len(selected)<2600


def test_issuer_discovery_reads_dated_results_without_model(tmp_path):
    from advisor.investigator.issuer import collect
    homepage='https://example.com';article=homepage+'/news/financial-results'
    profile=record(ticker='TEST',source='yahoo_profile',kind='profile',payload={'website':homepage},retrieved_at=NOW,observed_at=NOW)
    def fetcher(url):
        return {'links':[article] if url==homepage else [],'text':'Actual latest results','published_at':None if url==homepage else '2026-09-04T12:00:00Z','title':'Results'}
    records,errors=collect('TEST',[profile],tmp_path,fetcher=fetcher)
    assert len(records)==1 and not errors and records[0]['payload']['discovered_from']==homepage


def test_multiple_filings_and_estimate_vendors_are_not_independent_votes():
    a=fact('revenue',100,'2026-01-01','2026-03-31')
    b=fact('revenue',120,'2026-04-01','2026-06-30')
    assert a['independence']==b['independence']=='issuer:TEST'
    x=record(ticker='TEST',source='yahoo_estimates',kind='estimate',payload={},retrieved_at=NOW,observed_at=NOW)
    y=record(ticker='TEST',source='fmp_estimates',kind='estimate',payload={},retrieved_at=NOW,observed_at=NOW)
    assert x['independence']==y['independence']


def test_spawned_cli_preserves_completed_run_without_fetching(tmp_path):
    import subprocess,sys
    root=tmp_path/'intelligence'/'investigations'/'TEST'/'existingrun'
    root.mkdir(parents=True);(root/'report.json').write_text('{"unchanged": true}')
    result=subprocess.run([sys.executable,'-m','advisor.investigator','TEST','--scan-only','--data-dir',str(tmp_path),'--run-id','existingrun'],
                          capture_output=True,text=True,timeout=20)
    assert result.returncode!=0 and 'immutable' in result.stderr
    assert (root/'report.json').read_text()=='{"unchanged": true}'
    assert json.loads((root/'status.json').read_text())['state']=='failed'


def test_investigator_cannot_invoke_a_personal_assistant_account(monkeypatch):
    import subprocess
    from advisor.investigator.reasoner import invoke
    def forbidden(*args,**kwargs):raise AssertionError('An assistant subprocess must not launch')
    monkeypatch.setattr(subprocess,'run',forbidden)
    with pytest.raises(RuntimeError,match='No research model connected'):
        invoke('Investigate TEST',{'type':'object'},web=True)


def test_cashflow_change_uses_comparable_ytd_windows_and_binds_all_inputs():
    rows=[]
    for year,vals in [(2026,{'cfo':80,'net_income':100,'capex':20}), (2025,{'cfo':90,'net_income':75,'capex':10})]:
        for metric,value in vals.items():
            row=fact(metric,value,f'{year}-01-01',f'{year}-06-30')
            row['payload']['duration_class']='ytd';rows.append(row)
    rows.append(fact('net_income',30,'2026-04-01','2026-06-30'))
    result,cites=fundamental_metrics(annotate(rows,NOW))
    assert result['cashflow_net_income']==100
    assert result['cash_conversion']==pytest.approx(.8)
    assert result['prior_cash_conversion']==pytest.approx(1.2)
    assert result['cash_conversion_change_pp']==pytest.approx(-40)
    assert result['free_cash_flow_yoy_pct']==pytest.approx(-25)
    assert len(set(cites['cash_conversion_change_pp']))==4
    assert len(set(cites['free_cash_flow_yoy_pct']))==4
    for row in rows:
        if row['period_end']=='2025-06-30':row['period_start']='2025-04-01';row['payload']['duration_class']='quarter'
    result,_=fundamental_metrics(annotate(rows,NOW))
    assert 'prior_cash_conversion' not in result


def test_equity_value_trace_uses_outstanding_not_weighted_diluted_shares():
    from advisor.investigator.valuation import valuation
    quote=record(ticker='TEST',source='yahoo_price',kind='price',payload={'price':20,'currency':'USD'},observed_at=NOW,retrieved_at=NOW)
    shares=fact('shares_outstanding',1000,None,'2026-06-30',unit='shares')
    diluted=fact('shares_diluted',1100,'2026-04-01','2026-06-30',unit='shares')
    value=valuation(annotate([quote,shares,diluted],NOW),'TEST')
    assert value['equity_value_proxy']==20000
    assert value['equity_value_inputs']['reported_shares_outstanding']==1000
    assert 'not quarterly' in value['equity_value_inputs']['share_basis']


def test_earnings_bridge_separates_gross_cost_and_below_operating_changes():
    rows=[]
    for year,values in [(2026,{'revenue':100,'gross_profit':55,'op_income':21,'net_income':30}),
                        (2025,{'revenue':100,'gross_profit':60,'op_income':20,'net_income':20})]:
        rows += [fact(metric,value,f'{year}-04-01',f'{year}-06-30') for metric,value in values.items()]
    result,cites=fundamental_metrics(annotate(rows,NOW))
    assert result['gross_profit_margin_change_pp']==pytest.approx(-5)
    assert result['operating_cost_leverage_pp']==pytest.approx(6)
    assert result['op_income_margin_change_pp']==pytest.approx(1)
    assert result['below_operating_margin_change_pp']==pytest.approx(9)
    assert len(cites['earnings_bridge'])==8


def test_failed_synthesis_researches_missing_fact_before_revision(tmp_path,monkeypatch):
    from advisor.investigator import reasoner
    from advisor.investigator.temporal import utcnow
    now=utcnow()
    original=record(ticker='TEST',source='issuer_ir',kind='document',payload={'text':'Revenue increased 20 percent in the latest fiscal quarter.'},url='https://example.com/release',published_at=now,retrieved_at=now)
    additional=record(ticker='TEST',source='issuer_ir',kind='document',payload={'text':'Collections lagged because customers received longer payment terms.'},url='https://example.com/terms',published_at=now,retrieved_at=now)
    reviews=0;research_calls=[];revised_with=[]
    def research(*a,follow_up=None,**kw):
        research_calls.append(follow_up)
        return {'sources':[{'url':additional['url']}] if follow_up else [],'queries_run':['customer payment terms'] if follow_up else [],'unresolved':[]},{}
    monkeypatch.setattr(reasoner,'research',research)
    monkeypatch.setattr(reasoner,'ingest_web',lambda t,p:([additional] if p['sources'] else [],[]))
    monkeypatch.setattr(reasoner,'synthesize',lambda *a,**kw:(proposal(original),{}))
    def review(*a,**kw):
        nonlocal reviews
        reviews+=1
        return {'insights':[{'id':'one','supported':reviews>1,'reason':'Check payment terms'}],
                'action_supported':reviews>1,'missed_questions':['What are customer payment terms?'] if reviews==1 else []},{}
    monkeypatch.setattr(reasoner,'review',review)
    def revise(t,p,f,rows,a,**kw):
        revised_with.extend(r['url'] for r in rows)
        return proposal(original),{}
    monkeypatch.setattr(reasoner,'revise',revise)
    report=run('TEST',tmp_path,deep=True,collect={'fixture':lambda:([original],[])},model=lambda *a,**kw:None)
    assert research_calls==[None,None,['What are customer payment terms?']]
    assert additional['url'] in revised_with
    assert report['search']['stop_reason']=='bounded_follow_up_completed'
    assert report['search']['queries_run']==['customer payment terms']
    assert report['synthesis']['review_status']=='model_challenged' and reviews==2
