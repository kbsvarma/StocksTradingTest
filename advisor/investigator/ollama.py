"""Private host inference with schema validation and application-owned research tools."""
import json
import time
import requests

ENDPOINT = 'http://127.0.0.1:11434/api/chat'


def schema_guide(schema):
    """Tell the model the output contract, not just the decoding grammar.

    Quote alternatives already appear verbatim in evidence cards; repeating all
    source-specific enums would crowd the actual financial evidence out.
    """
    if 'oneOf' in schema:
        alternatives=schema['oneOf']
        if alternatives and all(set(x.get('properties',{}))=={'source_id','passage_id','use'} for x in alternatives):
            return {'source_id':'Select a supplied evidence alias','passage_id':'Select P01, P02, etc. belonging to that source card','use':'current or historical_comparison according to that source clock'}
        if alternatives and all(set(x.get('properties',{}))=={'source_id','excerpt','use'} for x in alternatives):
            return {'source_id':'Select a supplied evidence alias','excerpt':'Copy one exact quote option belonging to that source','use':'current or historical_comparison according to that source clock'}
        return {'oneOf':[schema_guide(x) for x in alternatives]}
    kind=schema.get('type')
    if kind=='object':return {key:schema_guide(value) for key,value in schema.get('properties',{}).items()}
    if kind=='array':return {'type':'array','minItems':schema.get('minItems',0),'maxItems':schema.get('maxItems','not specified'),'items':schema_guide(schema['items'])}
    if 'enum' in schema:return {'allowed_values':schema['enum']}
    return {key:value for key,value in schema.items() if key in ('type','minLength','maxLength','minimum','maximum')}


def generate(prompt, schema, config, timeout, post=None, output_tokens=8192):
    from .runtime import validate
    from .reasoner import SYSTEM
    model = config.get('ADVISOR_RESEARCH_MODEL', 'qwen3.5:9b')
    thinking = config.get('_local_thinking', True)
    large_context=model in {'qwen3.8:27b','qwen3.6:35b','qwen3.5:27b','gemma4:31b'}
    context_tokens=49152 if large_context else 32768
    character_limit=80000 if large_context else 52000
    prompt+='\nOUTPUT SCHEMA GUIDE (field types and allowed values, not content to repeat):\n'+json.dumps(schema_guide(schema),ensure_ascii=False,separators=(',',':'))
    # Never silently truncate evidence. Caller builds a selective, disclosed context.
    if len(prompt) > character_limit:
        raise ValueError(f'Local research context exceeds {character_limit:,} characters; select evidence before inference')
    body = {'model': model, 'stream': True, 'think': thinking, 'truncate': False, 'shift': False, 'format': schema,
            'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': prompt}],
            'options': {'num_ctx': context_tokens, 'num_predict': min(output_tokens, 8192 if thinking else 4096),
                        # Qwen's published defaults; low-temperature decoding can get stuck in repetition.
                        'temperature': 1.0 if thinking else 0.7, 'top_p': 0.95 if thinking else 0.8,
                        'top_k': 20, 'min_p': 0.0, 'presence_penalty': 1.5, 'repeat_penalty': 1,
                        'seed': 42, 'num_thread': 12}, 'keep_alive': '30m'}
    if model.startswith('gemma4'):
        body['options'].update(temperature=1.0,top_p=.95,top_k=64,presence_penalty=0,
                               num_batch=128)
        if model=='gemma4:12b':body['options']['num_gpu']=40
    if 'Qwen3.5-27B-GGUF' in model:body['options']['num_batch']=128
    sampling=config.get('ADVISOR_RESEARCH_SAMPLING','official')
    if sampling not in {'official','conservative'}:raise ValueError('Unknown local research sampling profile')
    if sampling=='conservative':body['options'].update(temperature=.2,top_p=.9,presence_penalty=0)
    started = time.monotonic()
    try:
        response = (post or requests.post)(ENDPOINT, json=body, timeout=(5, min(timeout,600)), allow_redirects=False, stream=True)
    except requests.RequestException as exc:
        raise RuntimeError('Local research model connection failed: ' + type(exc).__name__) from None
    if response.status_code != 200:
        response.close()
        raise RuntimeError('Local research model busy; retry after the active investigation' if response.status_code==503 else 'Local research model HTTP ' + str(response.status_code))
    try:
        parts=[];packet={}
        for line in response.iter_lines():
            if time.monotonic()-started>timeout:raise RuntimeError('Local research model exceeded its request time budget')
            if not line:continue
            packet=json.loads(line)
            if packet.get('error'):raise RuntimeError('Local research model stopped before completion')
            parts.append(packet.get('message',{}).get('content',''))
        if not packet.get('done') or packet.get('done_reason') != 'stop':
            raise RuntimeError('Local model output incomplete; no synthesis published '
                               f'(done={packet.get("done")}, reason={packet.get("done_reason")}, tokens={packet.get("eval_count")})')
        result = json.loads(''.join(parts))
        validate(result, schema)
    except requests.RequestException as exc:
        raise RuntimeError('Local model stream interrupted: '+type(exc).__name__) from None
    except (KeyError, TypeError, ValueError):
        raise RuntimeError('Local model returned invalid structured output') from None
    finally:
        response.close()
    return result, {'provider': 'ollama', 'model': model, 'elapsed_seconds': round(time.monotonic()-started, 2),
                    'tokens': {'input': packet.get('prompt_eval_count'), 'output': packet.get('eval_count')},
                    'prompt_eval_seconds': packet.get('prompt_eval_duration', 0)/1e9,
                    'generation_seconds': packet.get('eval_duration', 0)/1e9,
                    'context_characters': len(prompt), 'context_tokens': context_tokens,
                    'sampling': body['options'],
                    'thinking_enabled': thinking, 'cost_basis': 'Local hardware; no model API quota'}


def invoke(prompt, schema, *, config, web=False, timeout=240, post=None, research_queries=None, known_sources=None):
    from .gemini import invoke as application_research
    thinking=not web and config.get('ADVISOR_RESEARCH_REASONING','medium').lower() not in {'off','none','minimal'}
    return application_research(prompt, schema, config={**config, '_local_thinking': thinking}, web=web, timeout=timeout,
                                post=post, generate_fn=generate, research_queries=research_queries, known_sources=known_sources)
