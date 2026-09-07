"""Import an operator-reviewed thesis bound to current candidate evidence.

Usage: python -m advisor.research.underwriting --ticker XYZ --file thesis.json
       --reviewer operator-name [--ttl-hours 24]

This grants research priority eligibility only. It cannot enable execution or
set a calibrated probability. Review input is local, single-operator data.
"""
from __future__ import annotations
import argparse
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from advisor.suggestion_policy import ET
from advisor.research.datastore import RESEARCH_DIR
from advisor.research.suggestion_store import atomic_json, writer_lock, digest


def record(ticker, thesis, reviewer, *, research=RESEARCH_DIR, ttl_hours=24, now=None):
    now = now or datetime.now(ET)
    if not re.fullmatch(r'[A-Z0-9.\-]{1,24}', ticker):
        raise ValueError('Invalid ticker')
    if not reviewer.strip() or not 1 <= ttl_hours <= 168:
        raise ValueError('Reviewer and TTL of 1–168 hours are required')
    for key in ('why_now', 'catalyst', 'invalidation', 'horizon_rationale'):
        if not isinstance(thesis.get(key), str) or not thesis[key].strip():
            raise ValueError(f'Missing reviewed {key}')
    urls = thesis.get('source_urls')
    if not isinstance(urls, list) or not urls or any(not isinstance(u, str) or urlparse(u).scheme != 'https' or not urlparse(u).hostname for u in urls):
        raise ValueError('Dated thesis evidence requires HTTPS source URLs')
    with writer_lock(research):
        slate = json.loads((research / 'candidates_latest.json').read_text())
        if slate.get('release_id') != digest({'slate': slate.get('slate'), 'sources': slate.get('source_manifest')}):
            raise ValueError('Candidate release integrity failed')
        candidates = [r for r in slate['slate'] if r['ticker'] == ticker]
        if len(candidates) != 1 or not candidates[0].get('sources_verified'):
            raise ValueError('Exactly one candidate with verified sources is required')
        candidate = candidates[0]
        from advisor.research.candidate_provenance import observe
        if not observe(slate, now=now)["fresh"] or not all(
            observe(candidate.get("sources", {}).get(bucket, {}), now=now)["fresh"]
            for bucket in candidate.get("generators", {})):
            raise ValueError("Candidate evidence is stale")
        if thesis.get('direction') != (candidate.get('selection') or {}).get('direction'):
            raise ValueError('Thesis direction does not match current candidate')
        payload = {**thesis, 'reviewer': reviewer, 'reviewed_at': now.isoformat(),
                   'expires_at': (now + timedelta(hours=ttl_hours)).isoformat(),
                   'evidence_id': candidate['evidence_id'], 'review_type': 'operator_research_review'}
        from advisor.research.suggestion_templates import plan_for
        if not payload.get('plan'):
            raise ValueError('A reviewed plan is required')
        # Geometry validation is independent of any current quote/ATR.
        plan_for(payload['direction'], 1., 1., {'thesis': payload})
        atomic_json(research / 'underwriting' / f'{ticker}.json', payload)
        return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticker', required=True)
    parser.add_argument('--file', type=Path, required=True)
    parser.add_argument('--reviewer', required=True)
    parser.add_argument('--ttl-hours', type=int, default=24)
    args = parser.parse_args()
    record(args.ticker.upper(), json.loads(args.file.read_text()), args.reviewer, ttl_hours=args.ttl_hours)
    print('Reviewed thesis recorded. Rebuild candidates and picks to recheck priority eligibility.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
