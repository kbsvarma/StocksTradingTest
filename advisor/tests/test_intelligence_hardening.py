"""Cross-boundary failures discovered during the final implementation pass."""
from datetime import datetime, timezone
import json
import pytest
from advisor.intelligence.access import Principal
from advisor.intelligence.store import CallStore
from advisor.intelligence.events import process
from advisor.intelligence.adapters import snapshot, tracked_symbols
from advisor.intelligence.worker import run
from advisor.tests.test_intelligence_events_performance import approved_store, NOW
from advisor.tests.test_intelligence_contract import call
from advisor.tests.test_intelligence_playbooks import earnings_packet


def test_editor_cannot_evade_separation_by_claiming_different_author(tmp_path):
    with CallStore(tmp_path/'db',Principal('operator','model','admin')) as s:
        c=call(status='review_required',author='claimed-other-author')
        s.put(c)
        with pytest.raises(PermissionError,match='submitter'):
            s.review(c['call_id'],expected_revision=1,verdict='approve',reason='self review')


def test_reassessment_does_not_hide_live_risk_crossing(tmp_path):
    path,c=approved_store(tmp_path)
    q={'event_id':'entry','ticker':'TEST','kind':'quote','source':'fixture','observed_at':NOW.isoformat(),'px':99,'quote_status':'live'}
    with CallStore(path,Principal('service','a','service')) as s:
        assert process(s,q,now=NOW)[0]['state']=='active'
        process(s,{'event_id':'filing','ticker':'TEST','kind':'guidance','source':'issuer','observed_at':NOW.isoformat()},now=NOW)
        assert s.latest(c['call_id'])['status']=='review_required'
        assert process(s,{**q,'event_id':'stop','px':92},now=NOW)[0]['state']=='resolved'


def test_expiry_within_same_day_is_not_deduplicated_away(tmp_path):
    c=call(expires_at='2026-09-04T15:00:00Z')
    with CallStore(tmp_path/'db',Principal('service','model','service')) as s:
        s.put(c)
        first={'event_id':'clock:14','kind':'clock','source':'clock','observed_at':'2026-09-04T14:00:00Z'}
        assert process(s,first,now=NOW)==[]
        later=datetime(2026,9,4,16,tzinfo=timezone.utc)
        assert process(s,{**first,'event_id':'clock:16','observed_at':later.isoformat()},now=later)[0]['state']=='expired'


def test_security_navigation_rejects_path_traversal_from_artifact(tmp_path):
    (tmp_path/'research').mkdir()
    (tmp_path/'research'/'candidates_latest.json').write_text(json.dumps({'slate':[{'ticker':'../../private','detail':{}}]}))
    s=snapshot(tmp_path,now=NOW)
    assert s['candidates']==[] and any('navigation' in i for i in s['issues'])


def test_backdated_packet_cannot_be_issued_into_prospective_history(tmp_path):
    inbox=tmp_path/'intelligence'/'inbox';inbox.mkdir(parents=True)
    p=earnings_packet();p['decision_at']='2026-09-04T13:00:00Z'
    (inbox/'old.json').write_text(json.dumps(p))
    result=run(tmp_path,now=datetime(2026,9,10,14,tzinfo=timezone.utc))
    assert any('Backdated' in r['error'] for r in result['errors'])
    assert snapshot(tmp_path)['calls']==[]


def test_filing_intake_includes_after_hours_but_not_weekends():
    from advisor.events_poller import filing_poll_open,ET
    assert filing_poll_open(datetime(2026,9,4,18,tzinfo=ET))
    assert not filing_poll_open(datetime(2026,9,5,18,tzinfo=ET))
