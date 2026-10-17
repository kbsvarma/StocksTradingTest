"""Snapshot-bound evidence verification, with explicit semantic-review coverage.

Importers supply source text and structured facts. Matching a fact to a snapshot
does not prove the importer extracted it correctly; load-bearing claims also
require an independent review bound to the exact source and claim hashes.
No URL fetching is performed here, so untrusted documents cannot induce SSRF.
"""
from __future__ import annotations

from urllib.parse import urlsplit
from advisor.intelligence.contract import digest, timestamp, number

TIERS = {'primary', 'licensed_vendor', 'secondary'}


def seal_source(source):
    clean = {k: v for k, v in source.items() if k != 'sha256'}
    for field in ('source_id', 'url', 'published_at', 'retrieved_at', 'content', 'tier'):
        if not clean.get(field):
            raise ValueError(f'Source requires {field}')
    url = urlsplit(clean['url'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password:
        raise ValueError('Source requires credential-free HTTPS URL')
    if clean['tier'] not in TIERS:
        raise ValueError('Unknown source tier')
    if timestamp(clean['published_at']) > timestamp(clean['retrieved_at']):
        raise ValueError('Source published after retrieval')
    return {**clean, 'sha256': digest(clean)}


def claim_hash(claim):
    return digest({k: v for k, v in claim.items() if k != 'review'})


def review_claim(claim, source, *, reviewer, author, verdict='supported', at):
    if not reviewer or not author or reviewer == author or verdict not in {'supported', 'contradicted'}:
        raise ValueError('Independent named reviewer and supported/contradicted verdict required')
    if timestamp(at) < timestamp(source['retrieved_at']):
        raise ValueError('Review predates source retrieval')
    return {'claim_hash': claim_hash(claim), 'source_hash': source['sha256'],
            'reviewer': reviewer, 'author': author, 'verdict': verdict, 'reviewed_at': at}


def check_claim(claim, sources, *, as_of, author=None, max_age_hours=72):
    reasons, bound = [], False
    source = sources.get(claim.get('source_id'))
    if not source:
        return {'claim_id': claim.get('claim_id'), 'status': 'unsupported', 'reasons': ['Source snapshot missing'],
                'source_id':claim.get('source_id'), 'tier':None}
    try:
        sealed = seal_source(source)
        if source.get('sha256') != sealed['sha256']:
            reasons.append('Source hash mismatch')
        decision = timestamp(as_of)
        retrieved, published = timestamp(source['retrieved_at']), timestamp(source['published_at'])
        if retrieved > decision or published > decision:
            reasons.append('Source unavailable at decision time')
        if (decision - retrieved).total_seconds() > max_age_hours * 3600:
            reasons.append('Source verification expired')
        excerpt = claim.get('excerpt')
        if not isinstance(excerpt, str) or len(excerpt.strip()) < 8 or excerpt not in source['content']:
            reasons.append('Exact supporting excerpt missing from snapshot')
        if claim.get('kind') == 'numeric':
            fact = (source.get('facts') or {}).get(claim.get('field')) or {}
            if not number(claim.get('value')) or not number(fact.get('value')) or abs(claim['value'] - fact['value']) > 1e-8:
                reasons.append('Numeric value does not match source fact')
            for key in ('unit', 'period'):
                if not claim.get(key) or claim.get(key) != fact.get(key):
                    reasons.append(f'{key.title()} mismatch or missing')
        elif claim.get('kind') != 'text':
            reasons.append('Unsupported claim kind')
        bound = not reasons
        review = claim.get('review') or {}
        independent = (review.get('reviewer') and review.get('author')
                       and review['reviewer'] != review['author']
                       and (author is None or review['author'] == author))
        try:
            review_time_ok = retrieved <= timestamp(review['reviewed_at']) <= decision
        except (KeyError, ValueError, TypeError):
            review_time_ok = False
        reviewed = independent and review_time_ok and review.get('claim_hash') == claim_hash(claim) and review.get('source_hash') == source['sha256']
        if reviewed and review.get('verdict') == 'contradicted':
            reasons.append('Independent reviewer contradicted claim')
        elif not reviewed or review.get('verdict') != 'supported':
            reasons.append('Independent semantic review missing or mismatched')
    except (ValueError, TypeError, KeyError):
        reasons.append('Malformed source or claim')
    return {'claim_id': claim.get('claim_id'), 'status': 'verified' if not reasons else 'source_bound' if bound and not any('contradicted' in r for r in reasons) else 'unsupported',
            'reasons': reasons, 'source_id': source.get('source_id'), 'tier': source.get('tier'),
            'source_hash': source.get('sha256')}


def audit(claims, sources, *, as_of, author=None):
    rows = [check_claim(c, sources, as_of=as_of, author=author) for c in claims]
    required = [r for r, c in zip(rows, claims) if c.get('load_bearing', True)]
    identifiers = [c.get('claim_id') for c in claims]
    valid_ids = all(isinstance(x, str) and x for x in identifiers) and len(set(identifiers)) == len(identifiers)
    verified = sum(r['status'] == 'verified' for r in rows)
    return {'claims': rows, 'n_claims': len(rows), 'n_verified': verified,
            'coverage_pct': round(100 * verified / len(rows), 1) if rows else 0,
            'ready': bool(required) and valid_ids and all(r['status'] == 'verified' for r in required),
            'primary_present': any(r.get('tier') == 'primary' and r['status'] == 'verified' for r in rows),
            'identity_error': None if valid_ids else 'Missing or duplicate claim IDs',
            'method': 'Exact snapshot binding plus independent semantic review; source extraction remains auditable'}
