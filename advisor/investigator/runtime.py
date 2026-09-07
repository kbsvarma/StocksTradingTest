"""Application-owned Responses API runtime; never uses a personal assistant session."""
import json
import os
import math
import re
from pathlib import Path
import shlex
import requests

ENDPOINT='https://api.openai.com/v1/responses'


def config():
    # Dedicated optional service configuration, kept outside the repository.
    values={}
    path=Path(os.environ.get('ADVISOR_RESEARCH_ENV',str(Path.home()/'.advisor_research.env')))
    if path.is_file():
        for line in path.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith('#') or '=' not in line:continue
            key,value=line.split('=',1);key=key.strip().removeprefix('export ')
            if key not in {'OPENAI_API_KEY','ADVISOR_RESEARCH_MODEL','ADVISOR_RESEARCH_REASONING','GEMINI_API_KEY','ADVISOR_RESEARCH_PROVIDER','ADVISOR_RESEARCH_SAMPLING'}:continue
            parts=shlex.split(value,comments=True)
            if len(parts)==1:values[key]=parts[0]
    values.update({k:os.environ[k] for k in ('OPENAI_API_KEY','ADVISOR_RESEARCH_MODEL','ADVISOR_RESEARCH_REASONING','GEMINI_API_KEY','ADVISOR_RESEARCH_PROVIDER','ADVISOR_RESEARCH_SAMPLING') if os.environ.get(k)})
    return values


def provider(c):
    name=c.get('ADVISOR_RESEARCH_PROVIDER') or ('gemini' if c.get('GEMINI_API_KEY') else 'openai')
    if name not in {'gemini','openai','ollama'}:raise ValueError('Unsupported research provider')
    return name


def status():
    try:
        c=config();name=provider(c)
        return {'configured':name=='ollama' or bool(c.get('GEMINI_API_KEY' if name=='gemini' else 'OPENAI_API_KEY')),
                'provider':'Local model / Ollama' if name=='ollama' else 'Google model API' if name=='gemini' and c.get('ADVISOR_RESEARCH_MODEL','').startswith('gemma-') else 'Gemini API' if name=='gemini' else 'OpenAI API',
                'model':c.get('ADVISOR_RESEARCH_MODEL','qwen3.5:9b' if name=='ollama' else 'gemini-3.8-flash' if name=='gemini' else 'gpt-6-astra')}
    except (OSError,ValueError):
        return {'configured':False,'provider':'unconfigured','model':'unconfigured'}


def require_config():
    c=config();name=provider(c)
    if name!='ollama' and not c.get('GEMINI_API_KEY' if name=='gemini' else 'OPENAI_API_KEY'):
        raise RuntimeError('No research model connected: configure GEMINI_API_KEY or OPENAI_API_KEY in ~/.advisor_research.env. Personal assistant logins are not used.')
    return c


def validate(value,schema):
    """Validate the closed subset used by the investigator, including fake/test transports."""
    if 'oneOf' in schema:
        matches=0
        for alternative in schema['oneOf']:
            try:validate(value,alternative);matches+=1
            except ValueError:pass
        if matches!=1:raise ValueError('Research output does not match exactly one allowed source/time basis')
    kind=schema.get('type')
    valid={'object':isinstance(value,dict),'array':isinstance(value,list),'string':isinstance(value,str),
           'boolean':isinstance(value,bool),'integer':type(value) is int,
           'number':type(value) in (int,float) and math.isfinite(value)}
    if kind and not valid.get(kind,False):raise ValueError('Research output has invalid type')
    if 'enum' in schema and value not in schema['enum']:raise ValueError('Research output has invalid enum')
    if kind=='object':
        if any(k not in value for k in schema.get('required',[])):raise ValueError('Research output misses required field')
        props=schema.get('properties',{})
        if schema.get('additionalProperties') is False and set(value)-set(props):raise ValueError('Unexpected research output field')
        for k,v in value.items():
            if k in props:validate(v,props[k])
    elif kind=='string':
        if len(value)<schema.get('minLength',0) or len(value)>schema.get('maxLength',math.inf):raise ValueError('Research output string outside bounds')
        if 'pattern' in schema and not re.search(schema['pattern'],value):raise ValueError('Research output string outside pattern')
    elif kind=='array':
        if len(value)<schema.get('minItems',0) or len(value)>schema.get('maxItems',math.inf):raise ValueError('Research output array outside bounds')
        for v in value:validate(v,schema['items'])
    elif kind in {'number','integer'}:
        if value<schema.get('minimum',-math.inf) or value>schema.get('maximum',math.inf):raise ValueError('Research output number outside bounds')


def invoke(prompt,schema,*,web=False,timeout=240,budget=None,post=None,research_queries=None,known_sources=None):
    from .reasoner import SYSTEM
    c=require_config()
    if provider(c)=='ollama':
        from .ollama import invoke as local_invoke
        return local_invoke(prompt,schema,config=c,web=web,timeout=timeout,post=post,research_queries=research_queries,known_sources=known_sources)
    if provider(c)=='gemini':
        from .gemini import invoke as gemini_invoke
        return gemini_invoke(prompt,schema,config=c,web=web,timeout=timeout,post=post,research_queries=research_queries,known_sources=known_sources)
    if research_queries:prompt+='\nPrioritize these actual searches: '+json.dumps(research_queries)
    model=c.get('ADVISOR_RESEARCH_MODEL','gpt-6-astra')
    if len(prompt)>180_000:raise ValueError('Research context exceeds request limit')
    body={'model':model,'instructions':SYSTEM,'input':prompt,'store':False,
          'reasoning':{'effort':c.get('ADVISOR_RESEARCH_REASONING','high')},
          'max_output_tokens':12000,
          'text':{'format':{'type':'json_schema','name':'investment_research','strict':True,'schema':schema}}}
    if web:
        body.update(tools=[{'type':'web_search'}],tool_choice='required',max_tool_calls=12,
                    include=['web_search_call.action.sources'])
    # No automatic retries: an uncertain timeout may already have consumed API usage.
    try:
        response=(post or requests.post)(ENDPOINT,headers={'Authorization':'Bearer '+c['OPENAI_API_KEY'],
            'Content-Type':'application/json'},json=body,timeout=(10,timeout),allow_redirects=False)
    except requests.RequestException as exc:
        raise RuntimeError('Research API connection failed: '+type(exc).__name__) from None
    if response.status_code!=200:
        # Never echo a provider body, header or credential into a user-visible job log.
        reasons={401:'application API credential rejected',403:'model or project access denied',429:'rate or usage limit reached'}
        raise RuntimeError('Research API: '+reasons.get(response.status_code,f'HTTP {response.status_code}'))
    try:packet=response.json()
    except ValueError:raise RuntimeError("Research API returned unreadable JSON") from None
    if not isinstance(packet,dict):raise RuntimeError("Research API returned invalid response")
    if packet.get('status')!='completed':raise RuntimeError('Research API response incomplete; no synthesis published')
    outputs=packet.get('output',[]);texts=[];queries=[];actions=[];urls=[]
    for item in outputs:
        if item.get('type')=='web_search_call':
            action=item.get('action',{});actions.append(action)
            observed=action.get('queries') or ([action['query']] if action.get('query') else [])
            queries.extend(q for q in observed if isinstance(q,str))
            urls.extend(s['url'] for s in action.get('sources',[]) if isinstance(s,dict) and s.get('url'))
        if item.get('type')=='message':
            for part in item.get('content',[]):
                if part.get('type')=='refusal':raise RuntimeError('Research API declined the request')
                if part.get('type')=='output_text':texts.append(part['text'])
    if web and not actions:raise RuntimeError('Research response did not execute a web tool')
    try:
        result=json.loads(''.join(texts));validate(result,schema)
    except (ValueError,KeyError,TypeError):raise RuntimeError('Research API returned invalid structured output') from None
    usage={'provider':'openai','model':model,'response_id':packet.get('id'),'tokens':packet.get('usage',{}),
           'queries_observed':list(dict.fromkeys(queries)),'web_actions':actions,
           'source_urls':list(dict.fromkeys(urls)),'limits':{'output_tokens':12000,'web_tool_calls':12 if web else 0,'request_timeout_seconds':timeout},
           'cost_basis':'Provider token/tool billing; no dollar-cost estimate asserted'}
    return result,usage
