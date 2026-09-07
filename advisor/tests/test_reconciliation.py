"""Acceptance examples checked against dated financial statements, not model prose."""
from advisor.investigator.reconciliation import reconcile


def microsoft():
    return {'fundamentals':{
        'revenue_period':'2026-06-30','gross_profit_margin_change_pp':-1.387926,
        'op_income_margin_change_pp':.209639,'net_income_margin_change_pp':4.110740,
        'below_operating_margin_change_pp':3.901101,
        'cashflow_window':{'start':'2026-04-01','end':'2026-06-30'},'cashflow_unit':'USD',
        'cashflow_cfo':55_441_000_000,'cashflow_capex':35_802_000_000,
        'free_cash_flow':19_639_000_000,'prior_free_cash_flow':25_568_000_000,
        'cashflow_cfo_yoy_pct':29.9998},
        'fundamental_evidence':{'earnings_bridge':['quarter-current','quarter-prior'],
                               'cash_conversion':['cash-current'],'free_cash_flow_yoy_pct':['cash-current','cash-prior']}}


def test_microsoft_earnings_and_cash_flow_case():
    case={i['id']:i for i in reconcile(microsoft())['items']}
    assert 'below operating profit' in case['earnings']['consequence']
    assert 'operating margin +0.21pp' in case['earnings']['observation']
    cash=case['cash']
    assert 'change -5.929bn' in cash['observation']
    assert 'incremental capital expenditure' in cash['consequence']
    assert 'Free cash flow remains positive' in cash['consequence']
    assert cash['evidence_ids']==['cash-current','cash-prior']


def test_opposite_cash_outcome_does_not_inherit_microsoft_conclusion():
    a=microsoft();a['fundamentals'].update(free_cash_flow=30e9,cashflow_capex=25.441e9)
    cash=next(i for i in reconcile(a)['items'] if i['id']=='cash')
    assert 'reinvestment burden' not in cash['consequence']
    assert 'improved' in cash['consequence']


def test_bank_never_gets_generic_cash_distress_case():
    a=microsoft();a['metric_applicability']={'cash_flow_valuation':'Assess bank capital and normalized earnings.'}
    a['valuation']={'earnings_multiple_proxy':14.65,'evidence_ids':['bank-earnings','price']}
    case={i['id']:i for i in reconcile(a)['items']}
    assert 'cash' not in case
    assert '14.65' in case['valuation']['observation']
    assert 'normalization' in case['valuation']['consequence']


def test_missing_evidence_cannot_generate_an_authoritative_memo():
    a=microsoft();a['fundamental_evidence']={}
    assert reconcile(a)['items']==[]
