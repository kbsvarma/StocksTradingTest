"""Explicit report-to-research handoff; preserves analysis without self-approval."""
from datetime import timedelta
from pathlib import Path
from advisor.intelligence.contract import digest
from advisor.intelligence.playbooks import underwrite
from advisor.intelligence.store import CallStore
from .engine import atomic
from .temporal import utcnow


def queue_report(report,data,principal):
    principal.require('propose');now=utcnow();s=report['synthesis']
    # Deterministic episode means repeated button clicks cannot create independent calls.
    insights=s.get('insights') or []
    top=insights[0] if insights else {}
    thesis={'what_changed':top.get('what_changed') or '; '.join(f['detail'] for f in report['analysis']['findings'][:3]),
            'consensus':top.get('what_is_priced_in') or 'Verify the latest fiscal-period consensus and its basis.',
            'variant':s.get('action_reason') or 'Investigate the ranked evidence and competing hypotheses.',
            'mechanism':top.get('mechanism') or 'Underwrite the causal earnings and cash-flow mechanism.',
            'why_not_priced':top.get('what_is_priced_in') or 'Market mispricing has not yet been established.',
            'catalyst':'Verify and timestamp the catalyst: '+'; '.join(s.get('next_checks',[])[:3]),
            'invalidation':top.get('invalidation') or 'Specify a falsifiable thesis and a measured invalidation condition.',
            'contrary_evidence':top.get('counterargument') or '; '.join(f['detail'] for f in report['analysis']['findings'] if f['direction']=='bearish') or 'Investigate the strongest counter-thesis.'}
    thesis['what_changed']=f'Report as of {report["as_of"]}: '+thesis['what_changed']
    lineage={'investigation':{'ticker':report['ticker'],'run_id':report['run_id'],'snapshot_hash':report['snapshot_hash'],'as_of':report['as_of']}}
    packet={'ticker':report['ticker'],'episode':'investigation:'+report['run_id'],'author':principal.user,
            'playbook':'fundamental_revision','decision_at':now.isoformat(),'event_at':None,
            'expires_at':(now+timedelta(days=14)).isoformat(),'thesis':thesis,'claims':[],'sources':{},
            'observations':{},'plan':{},'publication_lineage':lineage,'submission_kind':'investigation_hypothesis'}
    call=underwrite(packet,as_of=now.isoformat())
    root=Path(data)/'intelligence'
    with CallStore(root/'calls.sqlite',principal) as store:
        if store.latest(call['call_id']):return call['call_id'],False
        store.put(call)
    atomic(root/'inbox'/f'{call["call_id"]}.json',packet)
    return call['call_id'],True
