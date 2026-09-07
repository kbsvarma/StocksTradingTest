import pytest
from advisor.investigator.provider_pacing import reserve


def test_processes_reserve_separate_request_slots(tmp_path):
    path=tmp_path/'gemini.lock';waits=[]
    assert reserve(path,clock=lambda:100,sleep=waits.append)==0
    assert reserve(path,clock=lambda:101,sleep=waits.append)==14
    assert reserve(path,clock=lambda:102,sleep=waits.append)==28
    assert waits==[14,28]
    with pytest.raises(RuntimeError,match='time budget'):reserve(path,clock=lambda:103,sleep=waits.append,timeout=20)


def test_daily_quota_is_distinguished_without_exposing_provider_message():
    from advisor.investigator.gemini import quota_reason
    class Response:
        def json(self):return {'error':{'message':'secret-project-token', 'details':[{'violations':[
            {'quotaId':'GenerateRequestsPerDayPerProjectPerModel-FreeTier','quotaValue':'20'}]}]}}
    text=quota_reason(Response())
    assert 'daily' in text and '20 requests' in text and 'minute spacing cannot resolve' in text
    assert 'secret' not in text
