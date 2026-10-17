import hashlib
import json
import pytest
from advisor.intelligence.publication_bridge import export_packets
from advisor.intelligence.worker import run
from advisor.tests.test_intelligence_playbooks import earnings_packet
from datetime import datetime, timezone


def setup(ctx):
    ctx.mkdir()
    p=earnings_packet()
    view={'instrument':'TEST','yf_ticker':'TEST','decision_key':'2026-09-04:TEST:long',
          'entry_px_low':98,'entry_px_high':100,'stop_px':95,'target_px':108,
          'time_stop':'2026-09-25','sizing':{'instrument_type':'equity'},'intelligence_packet':p}
    raw=json.dumps({'redteam':'applied','views':[view]}).encode()
    (ctx/'brief.json').write_bytes(raw)
    (ctx/'publication_commit.json').write_text(json.dumps({'brief_sha256':hashlib.sha256(raw).hexdigest(),'committed_at':'2026-09-04T13:00:00Z'}))
    checks=[{'claim_id':c['claim_id'],'verdict':'supported','source_url':p['sources'][c['source_id']]['url'],
             'verified_excerpt':c['excerpt'],'reviewed_at':'2026-09-04T12:30:00Z'} for c in p['claims']]
    (ctx/'redteam.json').write_text(json.dumps({'verdicts':[{'instrument':'TEST','verdict':'amend','intelligence_claim_checks':checks}]}))


def test_attested_bridge_routes_reviewable_call_with_final_geometry(tmp_path):
    ctx=tmp_path/'context';setup(ctx)
    data=tmp_path/'data'
    result=export_packets(ctx,data)
    assert result['exported']==['2026-09-04:TEST:long'] and not result['blocked']
    packet=json.loads(next((data/'intelligence'/'inbox').glob('*.json')).read_text())
    assert packet['author'].startswith('synthesis:') and packet['claims'][0]['review']['reviewer'].startswith('redteam:')
    assert packet['plan']['target']==108
    assert not run(data,now=datetime(2026,9,4,14,tzinfo=timezone.utc))['errors']


def test_changed_publication_cannot_export_packets(tmp_path):
    ctx=tmp_path/'context';setup(ctx)
    (ctx/'brief.json').write_text('{}')
    with pytest.raises(ValueError,match='attestation'):export_packets(ctx,tmp_path/'data')
