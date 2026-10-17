"""Fail-closed evidence gate for claiming a production investment engine."""
from __future__ import annotations


def assess(*,performance:dict,ranking:dict,correctness:dict,commercial:dict) -> dict:
    blockers=[]
    total=int(correctness.get('cases') or 0)
    critical=int(correctness.get('critical_errors') or 0)
    accuracy=correctness.get('material_claim_accuracy')
    if total<40: blockers.append(f'Independent research acceptance set has {total}/40 cases')
    if critical: blockers.append(f'Independent research acceptance set has {critical} critical errors')
    if not isinstance(accuracy,(int,float)) or accuracy<.95:
        blockers.append('Material-claim accuracy is below 95% or unmeasured')
    versions=ranking.get('by_scoring_version') or {}
    eligible_versions=[name for name,row in versions.items()
                       if int(row.get('n_nonoverlapping_windows') or 0)>=30]
    if not eligible_versions:blockers.append('Fewer than 30 non-overlapping prospective ranking windows')
    cohorts=[]
    for row in performance.get('groups') or []:
        interval=row.get('excess_interval')
        if (int(row.get('n_resolved') or 0)>=30 and
            int(row.get('n_benchmark_pairs') or 0)>=30 and
            int(row.get('n_issue_date_clusters') or 0)>=20 and
            isinstance(interval,list) and len(interval)==2 and
            isinstance(interval[0],(int,float)) and interval[0]>0 and
            isinstance(row.get('mean_net_pct'),(int,float)) and row['mean_net_pct']>0):
            cohorts.append(row)
    if not cohorts:blockers.append('No cost-complete prospective cohort has positive benchmark-relative uncertainty bounds')
    for key,label in (
        ('licensed_data_rights','Commercial data rights'),('tls_identity_controls','TLS and identity controls'),
        ('security_review','Independent security review'),('regulatory_review','Regulatory counsel review'),
        ('independent_model_approval','Independent model approval')):
        if commercial.get(key) is not True:blockers.append(label+' not complete')
    return {'status':'eligible_for_controlled_launch' if not blockers else 'blocked',
            'promotion_allowed':not blockers,'blockers':blockers,
            'eligible_ranking_versions':eligible_versions,
            'eligible_performance_cohorts':len(cohorts),
            'note':'Eligibility permits controlled human-reviewed research use; it never enables order routing.'}
