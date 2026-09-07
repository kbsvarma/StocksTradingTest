"""Bind candidate evidence to dated, hashed upstream releases."""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta
from advisor.suggestion_policy import ET, last_complete_session
from advisor.research.suggestion_store import digest


def observe(doc, *, now=None, max_days=None):
    now = now or datetime.now(ET)
    fresh = False
    stamp = doc.get('as_of') if isinstance(doc, dict) else None
    try:
        ts = datetime.fromisoformat(stamp)
        fresh = (ts.tzinfo is not None and ts <= now + timedelta(minutes=5)
                 and (now - ts <= timedelta(days=max_days) if max_days is not None
                      else ts.astimezone(ET).date().isoformat() >= last_complete_session(now)))
    except (TypeError, ValueError, KeyError):
        pass
    return {'as_of': stamp, 'sha256': digest(doc), 'fresh': bool(fresh)}


def enrich(entries, research, sources, health, *, now=None):
    now = now or datetime.now(ET)
    try:
        universe = json.loads((research / 'universe.json').read_text()).get('stocks', {})
    except (OSError, ValueError):
        universe = {}
    for ticker, entry in entries.items():
        if not re.fullmatch(r'[A-Za-z0-9.^=\-]{1,24}', ticker):
            raise ValueError('Invalid candidate ticker')
        if not entry['detail'].get('sector'):
            entry['detail']['sector'] = universe.get(ticker)
        entry['sources'] = {b: sources.get(b, {'fresh': False, 'reason': 'source unverified'})
                            for b in entry['generators']}
        # Stale optional evidence cannot affect direction or score. Retain it
        # alongside the candidate for diagnostics, never silently delete it.
        stale = {b: g for b, g in entry['generators'].items()
                 if not entry['sources'][b].get('fresh')}
        entry['stale_evidence'] = stale
        for b in stale:
            entry['generators'].pop(b)
            health[b] = {'live': False, 'n': 0, 'reason': 'source missing, stale or failed quality gate'}
        entry['buckets'] = list(entry['generators'])
        entry['sources_verified'] = bool(entry['generators']) and all(
            entry['sources'][b].get('fresh') for b in entry['generators'])
        entry['evidence_id'] = digest(entry['sources'])
        # Optional reviewed thesis is a versioned input, bound to THIS evidence
        # and direction. A stale dossier opinion cannot promote a new release.
        entry['underwriting'] = {}
        try:
            thesis = json.loads((research / 'underwriting' / f'{ticker}.json').read_text())
            expires = datetime.fromisoformat(thesis['expires_at'])
            reviewed = datetime.fromisoformat(thesis['reviewed_at'])
            if (thesis.get('evidence_id') == entry['evidence_id']
                    and isinstance(thesis.get('reviewer'), str) and thesis['reviewer'].strip()
                    and isinstance(thesis.get('source_urls'), list)
                    and bool(thesis['source_urls'])
                    and all(isinstance(url, str) and url.startswith('https://') for url in thesis['source_urls'])
                    and expires.tzinfo and reviewed.tzinfo
                    and reviewed <= now < expires
                    and expires - reviewed <= timedelta(days=7)):
                entry['underwriting'] = thesis
        except (OSError, ValueError, KeyError, TypeError):
            pass
