import pytest
from advisor.investigator import conditions
from advisor.investigator.runtime import validate
from advisor.investigator.grounding import hydrate


def fixture():
    return {'fundamentals':{'cash_conversion':.63,'cashflow_window':{'start':'2026-01-01','end':'2026-06-30','duration_class':'ytd'}},
        'fundamental_evidence':{'cash_conversion':['cfo','income']},'technicals':{'prior_20_high':230.475},
        'technical_evidence':['bars'],'technical_as_of':'2026-09-04','findings':[]}


def test_condition_uses_observed_baseline_and_cannot_invent_target():
    analysis=fixture()
    condition={'kind':'reported_metric','baseline_id':'cash_conversion','comparison':'above','window':'next comparable reporting window'}
    validate(condition,conditions.schema(analysis))
    text,ids,bound=conditions.render(condition,analysis)
    assert '0.63 times' in text and '2026-06-30' in text and ids==['cfo','income'] and bound
    with pytest.raises(ValueError):validate({**condition,'target':.8},conditions.schema(analysis))
    with pytest.raises(ValueError):conditions.render({**condition,'baseline_id':'invented'},analysis)
    with pytest.raises(ValueError):conditions.render({**condition,'window':'next completed trading session'},analysis)


def test_price_condition_preserves_dated_computed_level():
    condition={'kind':'market_level','baseline_id':'prior_20_high','comparison':'above','window':'next completed trading session'}
    validate(condition,conditions.schema(fixture()))
    text,ids,bound=conditions.render(condition,fixture())
    assert '230.47' in text and '2026-09-04' in text and ids==['bars'] and bound
    with pytest.raises(ValueError):validate({**condition,'kind':'reported_metric'},conditions.schema(fixture()))


def test_hydration_binds_condition_sources_and_rejects_hidden_event_cutoffs():
    rows=[{'id':key,'kind':'fundamental','payload':{'value':value},'temporal':{'state':'current'}} for key,value in [('cfo',63),('income',100)]]
    result=hydrate({'insights':[{'id':'one','evidence':[]}],
        'entry_conditions':[{'kind':'reported_metric','baseline_id':'cash_conversion','comparison':'above','window':'next comparable reporting window'}],
        'exit_conditions':[{'kind':'event','event':'Cash conversion falls below 0.5','source_to_check':'SEC 10-Q','time_window':'Next quarter'}]},fixture(),rows)
    assert {x['source_id'] for x in result['insights'][0]['evidence']}=={'cfo','income'}
    assert len(result['_condition_errors'])==1 and '0.5' in result['_condition_errors'][0]
    assert result['condition_basis'][0]['baseline_id']=='cash_conversion'
