"""Verify explicitly headed GAAP + adjustment = adjusted income tables.

Unsupported layouts remain research questions. No generic 'fully normalized'
earnings claim is inferred from a source-specific adjustment column.
"""
import math
import re
from decimal import Decimal

AMOUNT=r'\$\s*(\(?[+-]?\d[\d,]*(?:\.\d+)?\)?)'
ROW=re.compile(r'\bNet\s+Income\s+'+r'\s+'.join([AMOUNT]*6),re.I)


def amount(text):
    return Decimal(text.strip('()').replace(',',''))*(-1 if text.startswith('(') else 1)*1_000_000


def reconciliations(rows,ticker,fund,metric_sources):
    current=fund.get('net_income');growth=fund.get('net_income_yoy_pct');period=fund.get('net_income_period')
    if current is None or growth is None or growth==-100 or not period:return []
    prior=current/(1+growth/100)
    bound=metric_sources.get('net_income_yoy_pct',[])
    if len(bound)!=2:return []
    facts=[r for r in rows if r['id'] in bound and r.get('ticker')==ticker and r.get('kind')=='fundamental'
           and r['payload'].get('metric')=='net_income' and r['payload'].get('unit')=='USD'
           and r['payload'].get('duration_class')=='quarter' and r.get('temporal',{}).get('state')!='excluded']
    current_ids=[r['id'] for r in facts if r.get('period_end')==period and math.isclose(r['payload']['value'],current,abs_tol=1)]
    prior_ids=[r['id'] for r in facts if r.get('period_end','')<period and math.isclose(r['payload']['value'],prior,rel_tol=1e-9,abs_tol=1)]
    if not current_ids or not prior_ids:return []
    out=[];seen=set()
    for row in rows:
        p=row.get('payload',{});text=p.get('text','')
        if (row.get('ticker')!=ticker or row.get('kind')!='document' or row.get('authority') not in {'primary','issuer_statement'}
                or row.get('temporal',{}).get('state')!='current' or row.get('period_end')!=period
                or p.get('document_class')!='earnings_release'):continue
        for match in ROW.finditer(text):
            header=text[max(0,match.start()-600):match.start()]
            scope=re.search(r'Impact from ([^*\n]{1,80})\*',header)
            if not scope or not re.search(r'\$\s+in\s+millions',header,re.I):continue
            if not re.search(r'As Reported\s*\(GAAP\).*?Impact from.*?As Adjusted\s*\(non-GAAP\)',header,re.I):continue
            reported,adjustment,adjusted,old_reported,old_adjustment,old_adjusted=map(amount,match.groups())
            if not math.isclose(float(reported),current,rel_tol=1e-9,abs_tol=1) or not math.isclose(float(old_reported),prior,rel_tol=1e-9,abs_tol=1):continue
            if reported+adjustment!=adjusted or old_reported+old_adjustment!=old_adjusted:continue
            key=(scope[1],reported,adjustment,old_adjustment)
            if key in seen:continue
            seen.add(key)
            out.append({'scope':scope[1],'period_end':period,'unit':'USD',
                'reported_net_income':float(reported),'reconciliation_adjustment':float(adjustment),
                'adjusted_net_income':float(adjusted),'prior_reported_net_income':float(old_reported),
                'prior_reconciliation_adjustment':float(old_adjustment),'prior_adjusted_net_income':float(old_adjusted),
                'impact_on_reported_earnings':float(-adjustment),'prior_impact_on_reported_earnings':float(-old_adjustment),
                'year_over_year_change_in_impact':float(old_adjustment-adjustment),
                'adjusted_income_growth_pct':float((adjusted/old_adjusted-1)*100) if old_adjusted>0 else None,
                'source_excerpt':match[0],'header_excerpt':header,
                'evidence_ids':list(dict.fromkeys([row['id']]+current_ids+prior_ids)),
                'interpretation':'A negative reconciliation adjustment removes a gain already included in reported earnings. The source-defined adjustment scope is not a fully normalized recurring-earnings measure; other discrete items may remain.'})
    # Some issuers disclose a rounded reported/adjusted pair in the headline
    # instead of an additive adjustment table. Preserve that rounding explicitly.
    headline=re.compile(r'NET INCOME OF\s*\$\s*(\d+(?:\.\d+)?)\s+(BILLION|MILLION).{0,100}?'
        r'NET INCOME EXCLUDING (SIGNIFICANT ITEMS) OF\s*\$\s*(\d+(?:\.\d+)?)\s+(BILLION|MILLION)',re.I)
    for row in rows:
        p=row.get('payload',{});text=p.get('text','')
        if (row.get('ticker')!=ticker or row.get('kind')!='document' or row.get('authority') not in {'primary','issuer_statement'}
                or row.get('temporal',{}).get('state')!='current' or row.get('period_end')!=period
                or p.get('document_class')!='earnings_release'):continue
        for match in headline.finditer(text[:2500]):
            scale=Decimal(10)**(9 if match[2].lower()=='billion' else 6)
            stated=Decimal(match[1]);uncertainty=Decimal('0.5')*Decimal(10)**stated.as_tuple().exponent*scale
            if abs(Decimal(str(current))-stated*scale)>uncertainty:continue
            scale=Decimal(10)**(9 if match[5].lower()=='billion' else 6)
            adjusted=Decimal(match[4])*scale
            uncertainty=Decimal('0.5')*Decimal(10)**Decimal(match[4]).as_tuple().exponent*scale
            key=('headline',match[3].lower(),adjusted)
            if key in seen:continue
            seen.add(key)
            out.append({'scope':match[3].lower(),'period_end':period,'unit':'USD','rounded_headline':True,
                'reported_net_income':current,'adjusted_net_income':float(adjusted),
                'reconciliation_adjustment':float(adjusted-Decimal(str(current))),
                'impact_on_reported_earnings':float(Decimal(str(current))-adjusted),
                'impact_on_reported_earnings_uncertainty':float(uncertainty),
                'adjusted_share_of_reported_pct':float(adjusted/Decimal(str(current))*100) if current>0 else None,
                'source_excerpt':match[0],'header_excerpt':'Rounded issuer headline; excluding '+match[3].lower(),
                'evidence_ids':list(dict.fromkeys([row['id']]+current_ids)),
                'interpretation':'Company-adjusted earnings are rounded to the precision of the headline. The implied difference is after-tax net income, not a disclosed pre-tax gain. No prior adjusted-income comparison is established; this is not proof that all nonrecurring items are removed.'})
    return out
