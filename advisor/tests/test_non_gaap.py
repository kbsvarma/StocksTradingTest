import copy
from advisor.investigator.non_gaap import reconciliations


def fixture():
    fund={'net_income':120_000_000,'net_income_yoy_pct':50,'net_income_period':'2026-06-30'}
    rows=[{'id':'cur','ticker':'TEST','kind':'fundamental','period_end':'2026-06-30','temporal':{'state':'current'},
           'payload':{'metric':'net_income','value':120_000_000,'unit':'USD','duration_class':'quarter'}},
          {'id':'old','ticker':'TEST','kind':'fundamental','period_end':'2025-06-30','temporal':{'state':'context_only'},
           'payload':{'metric':'net_income','value':80_000_000,'unit':'USD','duration_class':'quarter'}},
          {'id':'release','ticker':'TEST','kind':'document','authority':'primary','period_end':'2026-06-30','temporal':{'state':'current'},
           'payload':{'document_class':'earnings_release','text':'($ in millions, except per share amounts) As Reported (GAAP) Impact from Investment X* As Adjusted (non-GAAP) Net Income $120 $(10) $110 $80 $5 $85'}}]
    return rows,fund,{'net_income_yoy_pct':['cur','old']}


def test_negative_adjustment_removes_a_gain_and_prior_loss_creates_positive_yoy_swing():
    rows,fund,ids=fixture();b=reconciliations(rows,'TEST',fund,ids)[0]
    assert b['scope']=='Investment X'
    assert b['impact_on_reported_earnings']==10_000_000
    assert b['prior_impact_on_reported_earnings']==-5_000_000
    assert b['year_over_year_change_in_impact']==15_000_000
    assert b['adjusted_net_income']==110_000_000
    assert b['evidence_ids']==['release','cur','old']


def test_wrong_units_nonadditive_tables_annual_numbers_and_old_releases_do_not_bind():
    rows,fund,ids=fixture()
    for before,after in [('in millions','in thousands'),('$110','$111'),('$120','$1,200')]:
        changed=copy.deepcopy(rows);changed[-1]['payload']['text']=changed[-1]['payload']['text'].replace(before,after)
        assert not reconciliations(changed,'TEST',fund,ids)
    rows[-1]['temporal']['state']='context_only'
    assert not reconciliations(rows,'TEST',fund,ids)
    rows[-1]['temporal']['state']='current'
    assert not reconciliations(rows,'TEST',fund,{'net_income_yoy_pct':['cur','unrelated']})


def test_rounded_adjusted_headline_preserves_precision_and_does_not_invent_prior_adjustments():
    rows,fund,ids=fixture()
    rows[-1]['payload']['text']='REPORTS NET INCOME OF $0.12 BILLION ($2.00 PER SHARE), NET INCOME EXCLUDING SIGNIFICANT ITEMS OF $0.10 BILLION ($1.67 PER SHARE)'
    b=reconciliations(rows,'TEST',fund,ids)[0]
    assert b['rounded_headline'] and b['adjusted_net_income']==100_000_000
    assert b['impact_on_reported_earnings']==20_000_000
    assert b['impact_on_reported_earnings_uncertainty']==5_000_000
    assert 'prior_adjusted_net_income' not in b
    rows[-1]['payload']['text']=rows[-1]['payload']['text'].replace('$0.12','$0.15')
    assert not reconciliations(rows,'TEST',fund,ids)
