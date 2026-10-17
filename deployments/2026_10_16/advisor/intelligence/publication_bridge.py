"""Import optional intelligence packets only from an attested published brief.

The independent red-team stage supplies per-claim checks. Code binds their
verdicts to snapshots and identities; the synthesis model cannot grant itself
verification or mutate the call store. This bridge creates reviewable research,
not automatic approval or orders.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from advisor.intelligence.contract import digest, timestamp
from advisor.intelligence.evidence import seal_source, review_claim
from advisor.research.suggestion_store import atomic_json


def export_packets(context_dir, data):
    ctx, data = Path(context_dir), Path(data)
    raw = (ctx/'brief.json').read_bytes()
    receipt = json.loads((ctx/'publication_commit.json').read_text())
    if hashlib.sha256(raw).hexdigest() != receipt.get('brief_sha256'):
        raise ValueError('Published brief attestation mismatch')
    brief = json.loads(raw)
    if brief.get('redteam') != 'applied': raise ValueError('Independent red-team application required')
    rt = json.loads((ctx/'redteam.json').read_text())
    verdicts = {v['instrument']: v for v in rt.get('verdicts', [])}
    result = {'exported': [], 'blocked': []}
    for view in brief.get('views', []):
        if not view.get('intelligence_packet'): continue
        try:
            p = deepcopy(view['intelligence_packet'])
            p.update(ticker=view['yf_ticker'], episode=view['decision_key'],
                     decision_at=receipt['committed_at'], author='synthesis:'+receipt['brief_sha256'][:12])
            # Final amended geometry is authoritative; drafts cannot retain
            # a target or position plan that the independent reviewer changed.
            p['plan'] = {**(p.get('plan') or {}), 'entry_low': view['entry_px_low'],
                         'entry_high': view['entry_px_high'], 'stop': view['stop_px'], 'target': view['target_px']}
            p['expires_at'] = str(view['time_stop'])[:10]+'T20:00:00+00:00'
            p['instrument_type'] = view['sizing']['instrument_type']
            verdict = verdicts.get(view['instrument']) or {}
            if verdict.get('verdict') not in {'survive','amend'}: raise ValueError('Surviving independent verdict required')
            checks = {r['claim_id']: r for r in verdict.get('intelligence_claim_checks', [])}
            sources = {key: seal_source(src) for key,src in (p.get('sources') or {}).items()}
            p['sources'] = sources
            for claim in p.get('claims') or []:
                claim.pop('review', None)
                check = checks.get(claim.get('claim_id')) or {}
                source = sources.get(claim.get('source_id')) or {}
                if (check.get('source_url') != source.get('url') or
                    check.get('verified_excerpt') != claim.get('excerpt') or
                    check.get('verdict') not in {'supported','contradicted'}):
                    continue  # exact blocker surfaces in the underwriting engine
                reviewed_at = check.get('reviewed_at')
                if timestamp(reviewed_at) > timestamp(receipt['committed_at']): raise ValueError('Future claim review')
                claim['review'] = review_claim(claim, source, reviewer='redteam:'+digest(rt)[:12],
                    author=p['author'], verdict=check['verdict'], at=reviewed_at)
            p['publication_lineage'] = {'brief_sha256': receipt['brief_sha256'], 'redteam_sha256': digest(rt)}
            from advisor.intelligence.adapters import read_rows
            origin=next((r for r in read_rows(data/'decision_journal.jsonl')
                         if r.get('type')=='view' and r.get('decision_key')==view['decision_key']),{})
            p['legacy_id']=origin.get('id')
            out = data/'intelligence'/'inbox'/f'{digest(view["decision_key"])[:24]}.json'
            atomic_json(out,p); result['exported'].append(view['decision_key'])
        except (KeyError,ValueError,TypeError) as exc:
            result['blocked'].append({'instrument':view.get('instrument'),'reason':str(exc)})
    atomic_json(data/'intelligence'/'bridge_status.json',result)
    return result
