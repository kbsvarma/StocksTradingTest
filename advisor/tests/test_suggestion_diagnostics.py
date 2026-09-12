from advisor.research.picks import _health_reason


def test_empty_priority_release_explains_leading_blockers():
    result={"n_priority":0,"picks":[
        {"triage":{"blockers":["Trading costs unavailable","Next earnings date unverified"]}},
        {"triage":{"blockers":["Trading costs unavailable"]}}]}
    reason=_health_reason(result)
    assert "Trading costs unavailable (2)" in reason
    assert "Next earnings date unverified (1)" in reason


def test_healthy_priority_release_has_no_degraded_reason():
    assert _health_reason({"n_priority":1,"picks":[]}) is None
