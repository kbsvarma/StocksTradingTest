"""Require original, current disclosure for quoted corporate financial quantities.

This is a corroboration gate, not a semantic proof: matching a number does not
establish the correct metric or period. Those still need the source review.
"""
import re
from decimal import Decimal


def quantities(text):
    found=set()
    for m in re.finditer(r'(?<![\w.])([+-]?\d[\d,]*(?:\.\d+)?)\s*(%|percent\b|billion\b|million\b)',text,re.I):
        value=Decimal(m[1].replace(',',''));unit=m[2].lower()
        if unit in {'million','billion'}:
            value*=Decimal(10)**(6 if unit=='million' else 9);unit='amount'
        else:unit='percent'
        found.add((value,unit))
    return found


def errors(insight,lookup):
    current_original=set();secondary=set()
    for citation in insight.get('evidence',[]):
        row=lookup.get(citation.get('source_id'))
        if not row or citation.get('use')!='current' or row.get('temporal',{}).get('state')!='current':continue
        payload=row.get('payload',{})
        if row.get('authority') in {'primary','issuer_statement'} and row.get('kind')=='document':
            current_original.update(quantities(payload.get('text','')))
        elif row.get('kind')=='document' and payload.get('dimension') in {'fundamentals','accounting'}:
            secondary.update(quantities(citation.get('excerpt','')))
    # Equivalent million/billion and decimal formatting is accepted; unrelated
    # or rounded figures are not silently treated as the same disclosure.
    return ['secondary_financial_quantity_needs_current_original_disclosure'] if secondary-current_original else []
