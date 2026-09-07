"""Deterministic research eligibility and lifecycle. No probability claims."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
POLICY_VERSION = 1


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def last_complete_session(now=None):
    """Exchange holidays and half-days, with a 30 minute final-bar allowance."""
    import exchange_calendars as xc
    import pandas as pd
    now = now or datetime.now(ET)
    if now.tzinfo is None:
        raise ValueError('timezone required')
    cal = xc.get_calendar('XNYS')
    day = pd.Timestamp(now.astimezone(ET).date())
    session = cal.date_to_session(day, direction='previous')
    if pd.Timestamp(now) < cal.session_close(session) + pd.Timedelta(minutes=30):
        session = cal.previous_session(session)
    return str(session.date())


def release_freshness(doc, now=None):
    now = now or datetime.now(ET)
    reasons = []
    try:
        required = last_complete_session(now)
        bar = doc.get('price_bar')
        if not bar or bar != required:
            reasons.append(f'Price bar {bar or "missing"}; required completed session {required}')
        issued = datetime.fromisoformat(doc['as_of'])
        if issued.tzinfo is None or issued > now + timedelta(minutes=5):
            reasons.append('Invalid or future issue timestamp')
        elif issued.date().isoformat() < required:
            reasons.append('Evidence release predates required session')
    except Exception as exc:
        reasons.append(f'Freshness unavailable: {type(exc).__name__}')
    return {'ok': not reasons, 'reasons': reasons}


def lifecycle(view, px=None, *, now=None, quote_fresh=True):
    """Same classifier for watch alerts and UI; invalidation precedes entry."""
    now = now or datetime.now(ET)
    if view.get('status') in ('withdrawn', 'resolved', 'superseded'):
        return view['status']
    if view.get('resolve_pending'):
        return 'invalidated' if view.get('hit_level') == 'stop' else 'target_observed'
    end = view.get('time_stop') or view.get('expires_on')
    if end and str(end)[:10] < now.astimezone(ET).date().isoformat():
        return 'expired'
    if not quote_fresh or not finite(px) or px <= 0:
        return 'quote_unavailable'
    stop, target = view.get('stop_px', view.get('stop')), view.get('target_px', view.get('target'))
    direction = view.get('direction', 'long')
    if direction not in ('long', 'short') or not all(finite(v) and v > 0 for v in (stop, target)):
        return 'blocked'
    long = direction == 'long'
    if (stop >= target) if long else (stop <= target):
        return 'blocked'
    if (px <= stop) if long else (px >= stop):
        return 'invalidated'
    if (px >= target) if long else (px <= target):
        return 'target_observed'
    lo, hi = view.get('entry_px_low', view.get('entry_low')), view.get('entry_px_high', view.get('entry_high'))
    if not all(finite(v) and v > 0 for v in (lo, hi)) or lo > hi:
        return 'blocked'
    return 'entry_zone' if lo <= px <= hi else 'waiting'


def assess_pick(pick, *, freshness, today):
    """Triage policy, not a fitted alpha model. Broad candidates remain measurable."""
    s = pick.get('selection') or {}
    blockers = list(freshness.get('reasons') or [])
    if s.get('conflicted'):
        blockers.append('Conflicting directional evidence requires review')
    if s.get('opposing'):
        blockers.append('Opposing evidence has not been resolved')
    if (s.get('lead_rank_pct') or 0) < .80:
        blockers.append('Lead signal below priority research threshold (80th percentile)')
    lead = (s.get('contributions') or {}).get(s.get('lead_bucket')) or {}
    population_n = lead.get('rank_population_n')
    if not isinstance(population_n, int) or population_n < 20 or lead.get('rank_population_scope') == 'nominated':
        blockers.append('Lead ranking population is too small, nominated-only or unverified')
    if (s.get('n_families') or 0) < 2:
        blockers.append('Needs corroboration from a second directional evidence family')
    if not pick.get('sector'):
        blockers.append('Sector classification unavailable')
    if not (pick.get('cost_screen') or {}).get('verified'):
        blockers.append('Trading costs unavailable')
    if pick.get('direction') == 'short':
        blockers.append('Borrow availability and holding cost unverified')
    next_eps = pick.get('next_earnings')
    if not next_eps:
        blockers.append('Next earnings date unverified')
    else:
        try:
            days = (datetime.fromisoformat(str(next_eps)[:10]).date() - today).days
            if days < 0:
                blockers.append('Earnings calendar is stale')
            elif days > 120:
                blockers.append('Next earnings date is outside the plausible quarterly calendar')
            elif days <= pick.get('horizon_td', 21) * 7 / 5:
                blockers.append('Earnings inside research horizon; event underwriting required')
        except (ValueError, TypeError):
            blockers.append('Earnings date invalid')
    evidence = pick.get('evidence') or {}
    if not evidence.get('sources_verified'):
        blockers.append('Load-bearing source freshness is unverified')
    thesis = evidence.get('thesis') or {}
    for key in ('why_now', 'catalyst', 'invalidation'):
        if not thesis.get(key):
            blockers.append(f'Thesis needs {key.replace("_", " ")}')
    reference_state = lifecycle(pick, pick.get('ref_px'))
    if reference_state in ('invalidated', 'target_observed', 'expired', 'blocked'):
        blockers.append('Reference price/plan state: ' + reference_state)
    blockers.extend(pick.get('exposure_blockers') or [])
    return {'policy_version': POLICY_VERSION,
            'group': 'priority_research' if not blockers else 'blocked' if not freshness.get('ok') or s.get('conflicted') or reference_state in ('invalidated', 'target_observed', 'expired', 'blocked') else 'watchlist',
            'blockers': list(dict.fromkeys(blockers)),
            'quality': {'supporting_families': s.get('n_families', 0),
                        'opposing_signals': len(s.get('opposing') or []),
                        'sources_verified': bool(evidence.get('sources_verified'))},
            'actionable': False}


def quote_is_fresh(quote, now=None):
    """Validate each quote event, not just the daemon file's write time."""
    now = now or datetime.now(ET)
    if not finite(quote.get('px')) or quote['px'] <= 0 or quote.get('kind') == 'close':
        return False
    if quote.get('market_ts'):
        from advisor.quoted import yf_quote_fresh
        return yf_quote_fresh(quote, now)
    try:
        ts = datetime.fromisoformat(quote['ts'])
        return ts.tzinfo is not None and 0 <= (now - ts).total_seconds() <= 120
    except (ValueError, TypeError, KeyError):
        return False


def exchange_session_open(now=None):
    import exchange_calendars as xc
    import pandas as pd
    now = now or datetime.now(ET)
    cal = xc.get_calendar('XNYS')
    session = pd.Timestamp(now.astimezone(ET).date())
    return bool(cal.is_session(session) and cal.session_open(session) <= pd.Timestamp(now) < cal.session_close(session))
