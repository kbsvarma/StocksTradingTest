"""Separate knowledge time, publication time, measurement period and event time."""
from datetime import datetime, timezone, timedelta
from advisor.intelligence.contract import digest, timestamp, number

MAX_AGE = {'price':5, 'technical':5, 'news':7, 'filing':120, 'fundamental':150,
           'estimate':7, 'short_interest':25, 'ownership':120, 'option':4,
           'macro':10, 'catalyst':30, 'profile':7, 'document':30, 'borrow':2}

def utcnow(): return datetime.now(timezone.utc)

def iso(value):
    if value is None: return None
    if isinstance(value, (int,float)): return datetime.fromtimestamp(value,timezone.utc).isoformat()
    if isinstance(value,datetime):
        if value.tzinfo is None: return value.date().isoformat()+'T23:59:59+00:00'
        return value.astimezone(timezone.utc).isoformat()
    raw=str(value)
    if len(raw)==10: return raw+'T23:59:59+00:00' # date-only availability is conservatively end-of-day
    return timestamp(raw).astimezone(timezone.utc).isoformat()

def record(*, ticker, source, kind, payload, retrieved_at, published_at=None,
           observed_at=None, period_start=None, period_end=None, event_at=None,
           url='', authority='secondary', independence=None, title='', clock_quality='explicit'):
    if kind not in MAX_AGE:raise ValueError('Unsupported evidence kind')
    if not isinstance(payload,dict):raise ValueError('Evidence payload must be an object')
    if kind=='fundamental':
        if not number(payload.get('value')) or not payload.get('metric') or not payload.get('unit'):
            raise ValueError('Fundamentals require a finite value, metric and unit')
        if payload.get('duration_class') not in {'instant','quarter','ytd','annual'}:raise ValueError('Fundamental duration class required')
    if period_start and period_end and timestamp(iso(period_start))>timestamp(iso(period_end)):raise ValueError('Measurement period is inverted')
    # Multiple filings or vendors repeating the same upstream information are not independent votes.
    if source in {'sec_facts','sec_filings','sec_exhibits','issuer_ir','press_wires','sec_quality','sec_dilution','sec_insiders','sec_ownership'}:
        independence='issuer:'+ticker
    elif kind=='estimate':independence='analyst_consensus:'+ticker
    elif kind in {'price','technical','option'}:independence='market:'+ticker
    row=dict(ticker=ticker,source=source,kind=kind,payload=payload,url=url,authority=authority,
             independence=independence or source,title=title,retrieved_at=iso(retrieved_at),
             published_at=iso(published_at),observed_at=iso(observed_at),period_start=period_start,
             period_end=period_end,event_at=iso(event_at),clock_quality=clock_quality)
    row['id']=digest(row)[:24]
    return row

def periodic_document(row):
    payload=row.get('payload',{})
    if row.get('kind')!='document':return False
    if payload.get('document_class')=='periodic_filing':return payload.get('form') in {'10-Q','10-K','20-F'}
    excerpt=payload.get('period_basis_excerpt','')
    return payload.get('document_class')=='earnings_release' and bool(excerpt) and excerpt in payload.get('text','')


def assess(row, as_of):
    at=timestamp(iso(as_of)); reasons=[]
    retrieved=timestamp(row['retrieved_at'])
    if retrieved>at: reasons.append('retrieved_after_cutoff')
    pub=timestamp(row['published_at']) if row.get('published_at') else None
    obs=timestamp(row['observed_at']) if row.get('observed_at') else None
    if pub and pub>at: reasons.append('published_after_cutoff')
    if obs and obs>at: reasons.append('observation_after_cutoff')
    kind=row['kind']
    periodic=periodic_document(row)
    max_age=150 if periodic else MAX_AGE.get(kind,7)
    if kind in {'news','filing','document'} and not pub: reasons.append('publication_unknown')
    if kind=='news' and not row.get('payload',{}).get('body_verified'): reasons.append('headline_discovery_only')
    if row.get('payload',{}).get('relevance')=='unverified': reasons.append('issuer_relevance_unverified')
    if (kind in {'fundamental','short_interest','ownership'} or periodic) and not row.get('period_end'):
        reasons.append('measurement_period_unknown')
    if row.get('clock_quality')=='observation_only': reasons.append('underlying_period_unverified')
    clock=obs or pub
    age=(at-clock).total_seconds()/86400 if clock else None
    if clock is None: reasons.append('source_clock_unknown')
    if age is not None and age>max_age: reasons.append('stale_observation')
    if row.get('period_end') and (kind in {'fundamental','short_interest','ownership'} or periodic):
        end=timestamp(iso(row['period_end']))
        period_age=(at-end).total_seconds()/86400
        if period_age>max_age: reasons.append('old_measurement_period')
        if end>at: reasons.append('future_measurement_period')
    # Republishing old news does not refresh the economic event.
    if kind in {'news','document'} and row.get('event_at') and not periodic:
        if (at-timestamp(row['event_at'])).days>MAX_AGE[kind]: reasons.append('old_event_recirculated')
    blocked=any(x.endswith('after_cutoff') or x=='future_measurement_period' for x in reasons)
    return {'state':'excluded' if blocked else 'context_only' if reasons else 'current',
            'age_days':round(age,2) if age is not None else None,'reasons':reasons}

def annotate(rows, as_of):
    rows=[dict(r,temporal=assess(r,as_of)) for r in rows]
    # Latest measurement per fundamental metric wins; a newly filed old quarter cannot supersede it.
    latest={}
    for r in rows:
        if r['kind']=='fundamental' and r['temporal']['state']=='current':
            key=(r['ticker'],r['payload'].get('metric'),r['payload'].get('duration_class'))
            latest[key]=max(latest.get(key,''),r.get('period_end') or '')
    for r in rows:
        key=(r['ticker'],r['payload'].get('metric'),r['payload'].get('duration_class'))
        if r['kind']=='fundamental' and r.get('period_end','')<latest.get(key,'') and r['temporal']['state']=='current':
            r['temporal']={'state':'context_only','age_days':r['temporal']['age_days'],'reasons':['superseded_measurement_period']}
    latest_filing={}
    for r in rows:
        if periodic_document(r) and r['temporal']['state']=='current':
            latest_filing[r['ticker']]=max(latest_filing.get(r['ticker'],''),r.get('period_end') or '')
    for r in rows:
        if (periodic_document(r) and
            r['temporal']['state']=='current' and (r.get('period_end') or '')<latest_filing.get(r['ticker'],'')):
            r['temporal']={'state':'context_only','age_days':r['temporal']['age_days'],'reasons':['superseded_measurement_period']}
    return rows
