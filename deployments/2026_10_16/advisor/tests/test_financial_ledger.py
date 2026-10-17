from advisor.investigator.financial_ledger import build


def fact(metric,value,duration='annual',start='2025-06-01',end='2026-05-31',unit='USD',state='current'):
    return {'id':metric+str(value),'ticker':'TEST','kind':'fundamental','authority':'primary','url':'https://example.com/filing',
        'temporal':{'state':state},'period_start':start,'period_end':end,'published_at':'2026-06-22',
        'payload':{'metric':metric,'value':value,'unit':unit,'duration_class':duration}}


def price():return {'id':'price','ticker':'TEST','kind':'price','temporal':{'state':'current'},'observed_at':'2026-09-04','url':'https://example.com/price','payload':{'price':158.78,'currency':'USD'}}


def test_annual_pe_is_calculated_with_period_and_actual_inputs():
    rows=[fact('eps_diluted',5.83,unit='USD/shares'),price(),
          {'ticker':'TEST','kind':'profile','payload':{'lastSplitDate':971395200}}]
    ledger=build(rows,'TEST');pe=next(e for e in ledger['entries'] if e['key']=='pe_fiscal_year')
    assert abs(pe['value']-27.23499)<.00001
    assert pe['period_end']=='2026-05-31'
    assert [i['value'] for i in pe['inputs']]==[158.78,5.83]


def test_negative_earnings_and_new_split_cannot_create_positive_pe():
    assert not any(e['unit']=='x' for e in build([fact('eps_diluted',-2,unit='USD/shares'),price()],'TEST')['entries'])
    rows=[fact('eps_diluted',5.83,unit='USD/shares'),price(),{'ticker':'TEST','kind':'profile','payload':{'lastSplitDate':'2026-08-01'}}]
    assert not any(e['key']=='pe_fiscal_year' for e in build(rows,'TEST')['entries'])


def test_cash_flow_requires_same_window_and_bank_does_not_get_industrial_fcf():
    rows=[fact('cfo',100),fact('capex',150,start='2026-03-01')]
    assert not any(e['key']=='fcf_annual' for e in build(rows,'TEST')['entries'])
    rows[1]=fact('capex',150)
    assert next(e['value'] for e in build(rows,'TEST')['entries'] if e['key']=='fcf_annual')==-50
    rows.append({'ticker':'TEST','kind':'profile','payload':{'industry':'Banks - Diversified'}})
    assert not any(e['key']=='fcf_annual' for e in build(rows,'TEST')['entries'])


def test_profit_growth_and_profit_margin_are_different_calculations():
    rows=[fact('revenue',200),fact('net_income',40),fact('net_income',20,start='2024-06-01',end='2025-05-31',state='context_only')]
    values={e['key']:e['value'] for e in build(rows,'TEST')['entries']}
    assert values['net_income_margin_annual']==20
    assert values['net_income_growth_annual']==100


def test_latest_release_does_not_turn_old_facts_into_current_results():
    rows=[fact('cash',31.289e9,duration='instant',start=None),{'ticker':'TEST','kind':'document','temporal':{'state':'current'},'period_end':'2026-08-31','payload':{'document_class':'earnings_release','text':'New results','markdown':'New results'}}]
    ledger=build(rows,'TEST')
    assert ledger['entries'][0]['period_status']=='older disclosed period'
    assert 'newer than structured' in ' '.join(ledger['gaps'])


def test_adjusted_annual_and_guidance_are_separate_and_quarter_not_annualized():
    rows=[price(),{'id':'doc','ticker':'TEST','kind':'document','authority':'primary','url':'https://example.com/results','published_at':'2026-06-10','temporal':{'state':'current'},'period_end':'2026-05-31',
        'payload':{'document_class':'earnings_release','text':'unused','markdown':'For fiscal year 2027, we raise our non-GAAP EPS guidance to $8.05.\n\nExcluding investment gains, Q4 non-GAAP EPS would be $2.03 and FY 2026 non-GAAP EPS would be $6.83.'}}]
    ratios=[e for e in build(rows,'TEST')['entries'] if e['unit']=='x']
    assert {round(e['value'],2) for e in ratios}=={19.72,23.25}
    assert all('FY2027' in e['period_label'] or 'FY2026' in e['period_label'] for e in ratios)


def test_bank_book_multiple_retains_price_and_disclosed_book_sources():
    from advisor.investigator.financial_ledger import markdown
    doc={'id':'book','ticker':'TEST','kind':'document','authority':'primary','url':'https://example.com/results',
        'period_end':'2026-06-30','temporal':{'state':'current'},
        'payload':{'document_class':'earnings_release','text':'Tangible book value per share 2 of $113.35, up 10% YoY.'}}
    rows=[price(),doc,{'ticker':'TEST','kind':'profile','payload':{'industry':'Banks - Diversified'}}]
    ledger=build(rows,'TEST')
    ratio=next(e for e in ledger['entries'] if e['key']=='price_tangible_book')
    assert abs(ratio['value']-158.78/113.35)<1e-6
    assert ratio['period_end']=='2026-06-30'
    assert len(ratio['source_urls'])==2
    assert '[Input 2](https://example.com/results)' in markdown(ledger)
    doc['authority']='secondary'
    assert not any(e['key']=='price_tangible_book' for e in build(rows,'TEST')['entries'])


def test_latest_fiscal_year_remains_dated_history_after_current_window():
    from advisor.investigator.temporal import utcnow
    from datetime import timedelta
    end=(utcnow().date()-timedelta(days=200)).isoformat()
    start=(utcnow().date()-timedelta(days=564)).isoformat()
    eps=fact('eps_diluted',2.73,start=start,end=end,unit='USD/shares',state='context_only')
    eps['temporal']['reasons']=['stale_observation','old_measurement_period']
    latest=(utcnow().date()-timedelta(days=20)).isoformat()
    release={'ticker':'TEST','kind':'document','period_end':latest,'temporal':{'state':'current'},'payload':{'document_class':'earnings_release'}}
    ratio=next(e for e in build([eps,price(),release],'TEST')['entries'] if e['key']=='pe_fiscal_year')
    assert ratio['period_end']==end and ratio['period_status']=='older disclosed period'
    eps['temporal']['reasons']=['publication_unknown']
    assert not any(e['key']=='pe_fiscal_year' for e in build([eps,price(),release],'TEST')['entries'])


def test_loss_maker_gets_sales_context_without_positive_pe():
    revenue=fact('revenue',1e9)
    shares=fact('shares_outstanding',1e8,duration='instant',start=None,unit='shares')
    rows=[price(),revenue,shares,fact('eps_diluted',-2,unit='USD/shares')]
    valuation={'equity_value_proxy':158.78e8,'equity_value_basis':'Dated share count proxy',
        'trailing':{'revenue':{'value':1e9,'end':'2026-05-31','ids':[revenue['id']],'method':'reported fiscal year'}}}
    ledger=build(rows,'TEST',valuation)
    assert not any(e['key']=='pe_fiscal_year' for e in ledger['entries'])
    ratio=next(e for e in ledger['entries'] if e['key']=='price_sales_proxy')
    assert ratio['value']==15.878
    assert 'not a profitability' in ratio['definition']
    assert len(ratio['inputs'])==3
