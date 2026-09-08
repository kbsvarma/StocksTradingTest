from advisor.investigator.market_context import markdown


def test_market_context_keeps_dates_units_and_estimates_distinct():
    def item(value,**kwargs):return {'value':value,'observed_at':'2026-09-04','source_urls':['https://example.com/market'],**kwargs}
    observations=[item({'calendar':{'Earnings Date':['2026-08-10','2026-09-10']}}),
        item({'shortPercentOfFloat':.0278,'shortRatio':1.37},period_end='2026-08-14'),
        item({'dataset':'eps_revisions','rows':[{'period':'0q','upLast30days':2,'downLast30days':0}]}),
        item({'dataset':'earnings_estimate','rows':[{'period':'0q','avg':1.73905,'currency':'USD'}]}),
        item({'rsi14':60.97,'distance_ma200_pct':-5.44,'relative_21d_pp':10.46,'benchmark':'SPY'},unit='calculated technical measures')]
    text=markdown(observations,'2026-09-08')
    assert '2026-08-10' not in text and '2026-09-10' in text
    assert 'provider estimate, not issuer-confirmed' in text
    assert '2.78% of float' in text and 'dated 2026-08-14' in text
    assert '2 upward / 0 downward' in text
    assert '1.74 USD' in text and 'accounting basis' in text
    assert '-5.44%' in text and '+10.46 pp' in text


def test_absent_market_values_are_not_fabricated():
    assert markdown([{'value':{},'observed_at':None}],'2026-09-08')==''
