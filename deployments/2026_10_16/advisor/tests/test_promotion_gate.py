from advisor.intelligence.promotion_gate import assess


def test_empty_evidence_cannot_promote():
    result=assess(performance={},ranking={},correctness={},commercial={})
    assert not result['promotion_allowed']
    assert len(result['blockers'])>=8


def test_gate_requires_positive_cost_complete_benchmark_relative_cohort():
    commercial={k:True for k in ('licensed_data_rights','tls_identity_controls','security_review','regulatory_review','independent_model_approval')}
    correctness={'cases':40,'critical_errors':0,'material_claim_accuracy':.99}
    ranking={'by_scoring_version':{'3':{'n_nonoverlapping_windows':30}}}
    performance={'groups':[{'n_resolved':30,'n_benchmark_pairs':30,'n_issue_date_clusters':20,
        'mean_net_pct':1.0,'excess_interval':[-.1,1.2]}]}
    assert not assess(performance=performance,ranking=ranking,correctness=correctness,commercial=commercial)['promotion_allowed']
    performance['groups'][0]['excess_interval']=[.1,1.2]
    result=assess(performance=performance,ranking=ranking,correctness=correctness,commercial=commercial)
    assert result['promotion_allowed']
    assert result['status']=='eligible_for_controlled_launch'
