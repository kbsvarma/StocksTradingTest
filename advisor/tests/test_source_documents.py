from datetime import datetime,timezone
from advisor.investigator.source_documents import earnings_metadata
from advisor.investigator.temporal import record,annotate


def release(text,published):
    metadata,period=earnings_metadata(text,published)
    return record(ticker='TEST',source='sec_exhibits',kind='document',payload={'text':text,**metadata},
        published_at=published,retrieved_at='2026-09-07T12:00:00Z',period_end=period)


def test_latest_earnings_release_stays_current_beyond_news_window():
    row=release('Results for the quarter ended June 30, 2026. Revenue increased.', '2026-07-29T20:00:00Z')
    assert row['period_end']=='2026-06-30'
    assert row['payload']['period_basis_excerpt'] in row['payload']['text']
    assert annotate([row],datetime(2026,9,7,13,tzinfo=timezone.utc))[0]['temporal']['state']=='current'


def test_earnings_period_is_not_inferred_from_a_fiscal_label_or_publication():
    assert earnings_metadata('Second-quarter fiscal 2027 results announced today.', '2026-08-26')==({},None)
    assert earnings_metadata('Quarter ended June 30, 2026.', None)==({},None)


def test_republished_old_quarter_and_superseded_release_are_not_current():
    rows=[release('Results for the quarter ended June 30, 2026.', '2026-07-29'),
          release('Results for the quarter ended March 31, 2026.', '2026-09-06')]
    result=annotate(rows,datetime(2026,9,7,13,tzinfo=timezone.utc))
    assert result[0]['temporal']['state']=='current'
    assert result[1]['temporal']['state']=='context_only'
    assert 'old_measurement_period' in result[1]['temporal']['reasons']


def test_calendar_quarter_header_needs_corroborating_end_date():
    text='SECOND-QUARTER 2026 RESULTS. Book value at June 30, 2026. See quarter ended March 31, 2026.'
    assert earnings_metadata(text,'2026-07-14')[1]=='2026-06-30'
    assert earnings_metadata('SECOND-QUARTER 2026 RESULTS.','2026-07-14')==({},None)
    assert earnings_metadata('SECOND-QUARTER 2026 RESULTS. Fiscal year. June 30, 2026.','2026-07-14')==({},None)


def test_visible_publication_does_not_use_measurement_or_scheduled_call_date():
    from advisor.investigator.source_documents import visible_publication
    assert visible_publication('REDMOND, Wash. — July 29, 2026 — Results for quarter ended June 30, 2026.')[0]=='2026-07-29'
    assert visible_publication('A conference call today, July 14, 2026, discusses results.')[0]=='2026-07-14'
    assert visible_publication('A conference call on July 14, 2026. Quarter ended June 30, 2026.')==(None,None)
