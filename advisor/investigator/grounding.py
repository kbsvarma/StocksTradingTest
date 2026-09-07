"""Keep calculated observations and valuation assumptions outside free-form generation."""
import copy
import json
import re


def decision_findings(analysis):
    return [f for f in analysis.get('findings',[]) if f.get('materiality',3)>=2]


def context_only_flags(analysis):
    return {f['id'] for f in analysis.get('findings',[]) if f.get('materiality',3)<2}


def decision_evidence(rows,analysis):
    low=context_only_flags(analysis)
    return [r for r in rows if not (
        ('inventory_divergence' in low and r['kind']=='fundamental' and r['payload'].get('metric')=='inventory') or
        ('estimate_revision' in low and r['kind']=='estimate'))]


def valuation_statement(value):
    scenarios=value.get('implied_growth_sensitivity') or []
    valid=[s for s in scenarios if s.get('required_growth_pct') is not None]
    if not valid:return value.get('sector_method') or value.get('interpretation') or 'Price attractiveness is not established by the available valuation inputs.'
    s=valid[0]
    pairs='; '.join(f'{x["required_growth_pct"]:.1f}% at a {x["discount_rate"]:.0%} discount rate' for x in valid)
    return (f'The dated equity-value proxy requires annual free-cash-flow growth of {pairs}, over {s["years"]} years, '
            f'with {s["terminal_growth"]:.0%} terminal growth. These are sensitivity assumptions, not observed investor beliefs, revenue-growth requirements or perpetual high-growth forecasts.')


def with_valuation_finding(analysis):
    result=copy.deepcopy(analysis)
    value=result.get('valuation',{})
    if value.get('implied_growth_sensitivity') and value.get('evidence_ids'):
        result.setdefault('findings',[]).append({'id':'valuation_sensitivity','title':'Cash-flow growth required by the price',
            'detail':valuation_statement(value),'direction':'neutral','materiality':3,'dimension':'valuation',
            'evidence_ids':value['evidence_ids']})
    return result


def output_schema(base,analysis):
    schema=copy.deepcopy(base)
    schema['properties'].pop('summary',None)
    schema['required']=[x for x in schema['required'] if x!='summary']
    insight=schema['properties']['insights']['items']
    for field in ('what_changed','what_is_priced_in'):
        insight['properties'].pop(field,None)
        insight['required'].remove(field)
    ids=[f['id'] for f in decision_findings(analysis)]
    insight['properties']['finding_ids']={'type':'array','items':{'type':'string','enum':ids}} if ids else {'type':'array','items':{'type':'string'},'maxItems':0}
    insight['required'].append('finding_ids')
    # Constrained decoding follows property order. Ground premises before prose,
    # rather than selecting citations after an already-authored conclusion.
    order=('id','finding_ids','evidence','title','mechanism','counterargument','direction','invalidation','horizon','materiality')
    insight['properties']={key:insight['properties'][key] for key in order}
    insight['required']=list(order)
    schema['properties']['insights'].update(minItems=1,maxItems=2)
    insight['properties']['evidence']['maxItems']=min(3,insight['properties']['evidence'].get('maxItems',3))
    for field,limit in (('title',180),('mechanism',1500),('counterargument',1000),('invalidation',600),('horizon',80)):
        insight['properties'][field]={**insight['properties'][field],'maxLength':limit}
    insight['properties']['mechanism']['minLength']=40
    insight['properties']['counterargument']['minLength']=20
    schema['properties']['action_reason']={**schema['properties']['action_reason'],'maxLength':1500}
    for field in ('entry_conditions','exit_conditions','next_checks','contradictions'):
        schema['properties'][field]['maxItems']=3
        schema['properties'][field]['items']={**schema['properties'][field]['items'],'maxLength':1000 if field=='contradictions' else 600}
    return schema


def hydrate(proposal,analysis,rows):
    result=copy.deepcopy(proposal);lookup={r['id']:r for r in rows};findings={f['id']:f for f in analysis.get('findings',[])}
    for insight in result.get('insights',[]):
        chosen=insight.pop('finding_ids',[])
        if any(key not in findings for key in chosen):raise ValueError('Unknown calculated finding')
        observations=[];citations=insight.setdefault('evidence',[])
        documents=[]
        for citation in citations:
            row=lookup.get(citation.get('source_id'))
            if row and row['kind']=='document':documents.append('Source disclosure: '+citation.get('excerpt',''))
        for key in dict.fromkeys(chosen):
            finding=findings[key];observations.append(finding['detail'])
            for ident in finding['evidence_ids']:
                if ident not in lookup:raise ValueError('Calculated finding has missing source evidence')
                row=lookup[ident];payload=row['payload']
                if 'value' in payload:excerpt='"value": '+json.dumps(payload['value'])
                elif 'text' in payload:excerpt=payload['text'][:250]
                elif payload.get('bars'):excerpt=json.dumps(payload['bars'][-1],ensure_ascii=False)[1:-1][:250]
                else:excerpt=json.dumps(payload,ensure_ascii=False)[:250]
                citations.append({'source_id':ident,'excerpt':excerpt,
                    'use':'current' if row['temporal']['state']=='current' else 'historical_comparison'})
        if not observations and not documents:
            insight['_grounding_error']='An insight needs a calculated observation or a source disclosure'
            observations.append('No grounded observation established for this proposed insight.')
        insight['what_changed']=' '.join(observations+documents)
        insight['what_is_priced_in']=valuation_statement(analysis.get('valuation',{})) if 'valuation_sensitivity' in chosen else 'No separate market-expectations claim is established for this mechanism.'
        unique={}
        for c in citations:unique[(c['source_id'],c['excerpt'],c['use'])]=c
        insight['evidence']=list(unique.values())
    result['_condition_errors']=condition_errors(result,analysis)
    conditions=result.get('entry_conditions',[])+result.get('exit_conditions',[])+[i.get('invalidation','') for i in result.get('insights',[])]
    if result.get('insights') and any(re.search(r'\d',x) and re.search(r'high|low|average|breakout|breakdown',x,re.I) for x in conditions):
        ticker=analysis.get('issuer_identity',{}).get('ticker')
        history=next((r for r in rows if r['kind']=='technical' and r.get('ticker')==ticker and r['temporal']['state']=='current' and r['payload'].get('bars')),None)
        if history:
            result['insights'][0]['evidence'].append({'source_id':history['id'],'excerpt':json.dumps(history['payload']['bars'][-1],ensure_ascii=False)[1:-1][:250],'use':'current'})
        else:result['_condition_errors'].append('Technical condition lacks current issuer price-history evidence.')
    result['summary']=result.get('action_reason','')
    return result


def condition_errors(proposal,analysis):
    """A model may select dated technical levels, but cannot invent other cutoffs."""
    errors=[]
    technical=analysis.get('technicals',{})
    fields=proposal.get('entry_conditions',[])+proposal.get('exit_conditions',[])+[i.get('invalidation','') for i in proposal.get('insights',[])]
    for condition in fields:
        remaining=condition
        # Reporting horizons are not valuation/risk thresholds.
        remaining=re.sub(r'\bFY\s*20\d{2}\b|\bQ[1-4](?:\s+(?:FY)?20\d{2})?\b|\b20\d{2}-\d{2}-\d{2}\b|\b\d+[-\s]*(?:quarters?|months?|years?|weeks?|days?|sessions?)\b','',remaining,flags=re.I)
        for key,words in (('prior_20_high',('high','breakout')),('prior_20_low',('low','breakdown')),('ma200',('average','ma')),('ma50',('average','ma'))):
            value=technical.get(key)
            if value is not None and any(w in remaining.lower() for w in words):
                alternatives=sorted({str(value),f'{value:.2f}'},key=len,reverse=True)
                remaining=re.sub(r'(?<![\d.])(?:'+'|'.join(re.escape(v) for v in alternatives)+r')(?![\d.])','',remaining.replace(',',''))
        word_number=r'\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)\b.{0,35}\b(?:percent|dollars|basis points|times)\b'
        if re.search(r'\d',remaining) or re.search(word_number,remaining,re.I):
            errors.append('Unverified numerical condition: '+condition+' Use an observable qualitative condition or an exact dated computed technical level; do not invent a cutoff.')
    return errors
