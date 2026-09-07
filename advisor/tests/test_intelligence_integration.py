import json
from datetime import datetime, timezone
from advisor.intelligence.adapters import snapshot
from advisor.intelligence.worker import run
from advisor.tests.test_intelligence_playbooks import earnings_packet

NOW = datetime(2026, 9, 4, 14, tzinfo=timezone.utc)


def test_empty_terminal_snapshot_has_honest_missing_state_and_no_writes(tmp_path):
    s = snapshot(tmp_path, now=NOW)
    assert s['calls'] == [] and s['freshness']['status'] == 'missing'
    assert list(tmp_path.iterdir()) == []


def test_worker_ingests_one_episode_idempotently_without_self_approval(tmp_path):
    inbox = tmp_path/'intelligence'/'inbox'; inbox.mkdir(parents=True)
    p = earnings_packet(); p['decision_at'] = '2026-09-04T13:00:00Z'
    (inbox/'test.json').write_text(json.dumps(p))
    r = run(tmp_path, now=NOW)
    assert not r['errors'] and r['packets_ingested'] == 1
    assert run(tmp_path, now=NOW)['packets_ingested'] == 0
    s = snapshot(tmp_path, now=NOW)
    assert len(s['calls']) == 1 and s['calls'][0]['status'] == 'review_required'


def test_corrupt_packet_is_visible_and_does_not_break_other_ingest(tmp_path):
    inbox = tmp_path/'intelligence'/'inbox'; inbox.mkdir(parents=True)
    (inbox/'bad.json').write_text('{broken')
    r = run(tmp_path, now=NOW)
    assert r['status'] == 'degraded' and r['errors'][0]['file'] == 'bad.json'


def test_legacy_projection_keeps_original_issue_time_and_cannot_promote(tmp_path):
    rows = [{'id': 'x', 'type': 'view', 'yf_ticker': 'TEST', 'instrument': 'TEST', 'ts': '2026-07-01T12:00:00Z',
             'time_stop': '2026-07-20', 'thesis': 'A historical thesis', 'direction': 'long'},
            {'id': 'x', 'type': 'resolve', 'status': 'closed', 'ts': '2026-07-21T12:00:00Z'}]
    (tmp_path/'decision_journal.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    c = snapshot(tmp_path, now=NOW)['calls'][0]
    assert c['issued_at'].startswith('2026-07-01') and c['status'] == 'resolved'
    assert c['recommendation_class'] == 'research_idea'
