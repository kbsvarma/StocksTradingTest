from datetime import datetime,timezone
from advisor.investigator.temporal import annotate,record


def test_calendarized_article_cannot_supersede_original_fiscal_quarter():
    now=datetime(2026,9,7,tzinfo=timezone.utc)
    def release(ident,end,authority):
        excerpt='Quarter ended '+end
        return record(ticker='TEST',source=ident,kind='document',authority=authority,
            retrieved_at=now,published_at='2026-08-01',period_end=end,
            payload={'document_class':'earnings_release','period_basis_excerpt':excerpt,'text':excerpt})
    rows=annotate([release('issuer','2026-06-27','primary'),release('article','2026-06-30','reported'),
                   release('old','2026-03-28','primary')],now)
    assert rows[0]['temporal']['state']=='current'
    assert rows[1]['temporal']['state']=='context_only'
    assert rows[1]['temporal']['reasons']==['financial_period_disagrees_with_original_disclosure']
    assert rows[2]['temporal']['state']=='context_only'
