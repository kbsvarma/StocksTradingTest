from bs4 import BeautifulSoup
from advisor.investigator.share_classes import cover_shares


def document(classes):
    html='<xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>'
    for n,(label,value,date) in enumerate(classes):
        member=f'<xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">{label}</xbrldi:explicitMember>' if label else ''
        html+=f'<xbrli:context id="c{n}"><xbrli:entity>{member}</xbrli:entity><xbrli:period><xbrli:instant>{date}</xbrli:instant></xbrli:period></xbrli:context><ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c{n}" unitRef="shares" scale="6">{value}</ix:nonFraction>'
    return BeautifulSoup(html,'html.parser')


def test_common_classes_sum_at_same_date_without_double_counting():
    soup=document([('ClassA','5,868','2026-07-15'),('ClassB','835','2026-07-15'),('ClassC','5,527','2026-07-15'),('ClassA','5,868','2026-07-15')])
    result=cover_shares(soup)
    assert result['value']==12_230_000_000 and len(result['classes'])==3
    assert result['period_end']=='2026-07-15'


def test_aggregate_wins_and_mixed_dates_cannot_be_summed():
    assert cover_shares(document([('ClassA','5','2026-07-15'),('ClassB','7','2026-07-15'),('','12','2026-07-15')]))['value']==12_000_000
    assert cover_shares(document([('ClassA','5','2026-07-15'),('ClassB','7','2026-06-30')])) is None
    assert cover_shares(document([('ClassA','5','2026-07-15'),('ClassA','6','2026-07-15')])) is None
