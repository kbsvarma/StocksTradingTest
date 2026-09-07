from datetime import datetime,timezone
from advisor.investigator.source_documents import earnings_call_metadata
from advisor.investigator.temporal import record,annotate


def test_transcript_clock_preserves_unknown_publication_and_does_not_backdate_knowledge():
    title='Example Fiscal Year 2026 Fourth Quarter Earnings Conference Call'
    text=title+' Wednesday, July 29, 2026 Transcript '+('Management prepared remarks. '*100)
    meta=earnings_call_metadata(title,text)
    event=meta.pop('event_at')
    now=datetime(2026,9,7,tzinfo=timezone.utc)
    row=record(ticker='TEST',source='issuer_ir',kind='document',payload={**meta,'text':text},
               event_at=event,observed_at=now,retrieved_at=now,authority='issuer_statement')
    current=annotate([row],now)[0]
    assert current['published_at'] is None and current['period_end'] is None
    assert current['temporal']['state']=='current'
    assert annotate([row],datetime(2026,8,1,tzinfo=timezone.utc))[0]['temporal']['state']=='excluded'
    assert annotate([row],datetime(2027,2,1,tzinfo=timezone.utc))[0]['temporal']['state']=='context_only'


def test_calendar_listing_is_not_a_public_transcript():
    assert earnings_call_metadata('Earnings conference call','Wednesday, July 29, 2026 Register here')=={}


def test_new_call_supersedes_old_call_without_future_event_superseding_either():
    now=datetime(2026,9,7,tzinfo=timezone.utc)
    def call(event):
        return record(ticker='TEST',source='issuer_ir',kind='document',
            payload={'document_class':'earnings_call','event_basis_excerpt':event,'text':'Transcript '+event},
            retrieved_at=now,observed_at=now,event_at=event,authority='issuer_statement')
    rows=annotate([call('2026-05-15'),call('2026-08-01'),call('2026-10-01')],now)
    assert rows[0]['temporal']['state']=='context_only'
    assert 'superseded_earnings_call' in rows[0]['temporal']['reasons']
    assert rows[1]['temporal']['state']=='current'
    assert rows[2]['temporal']['state']=='excluded'
    assert 'event_after_cutoff' in rows[2]['temporal']['reasons']
