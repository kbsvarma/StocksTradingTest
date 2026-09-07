"""Ad-hoc investigation orchestration; immutable runs and explicit search accounting."""
from __future__ import annotations
import json
import os
import time
import uuid
from pathlib import Path
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from advisor.intelligence.contract import digest
from . import collectors, reasoner
from .temporal import utcnow, annotate
from .analysis import analyze
from .catalog import inventory


def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(collectors.clean(value),indent=2,allow_nan=False));tmp.replace(path)


def benchmark(ticker):
    import yfinance as yf
    tk=yf.Ticker(ticker);hist=tk.history(period='2y',auto_adjust=True,actions=False,timeout=15)
    if hist.empty:return [],['Benchmark bars unavailable']
    from .temporal import record
    return [record(ticker=ticker,source='yahoo_price',kind='technical',payload={'bars':collectors.frame_rows(hist),'adjusted':True},retrieved_at=utcnow(),observed_at=hist.index[-1].date().isoformat(),url=f'https://finance.yahoo.com/quote/{ticker}/',title='Benchmark adjusted daily bars')],[]


def run(ticker,data,*,deep=False,progress=None,run_id=None,collect=None,model=reasoner.invoke):
    if deep and model is reasoner.invoke:
        from .runtime import require_config
        require_config()
    ticker=collectors.symbol(ticker);data=Path(data);started=utcnow();run_id=run_id or uuid.uuid4().hex
    if not run_id.isalnum() or len(run_id)>64:raise ValueError('Invalid run identifier')
    root=data/'intelligence'/'investigations'/ticker/run_id;root.mkdir(parents=True,exist_ok=True)
    if (root/'report.json').exists():raise ValueError('An investigation run is immutable; use a new run identifier')
    try:previous=load_latest(data,ticker)
    except FileNotFoundError:previous=None
    rows=[];errors=[];stages=[];model_usage=[];searches=[];rejections=[];relationships=[]
    def update(stage,detail):
        item={'stage':stage,'detail':detail,'at':utcnow().isoformat()};stages.append(item)
        atomic(root/'status.json',{'run_id':run_id,'ticker':ticker,'state':'running','started_at':started.isoformat(),'updated_at':item['at'],'stage':stage,'detail':detail})
        if progress:progress(item)
    update('collect','Collecting timestamped fundamentals, filings, market structure, estimates, news and macro')
    jobs=collect or {'sec':lambda:collectors.sec(ticker,data),'yahoo':lambda:collectors.yahoo(ticker),
                    'provider_news_estimates':lambda:collectors.credential_sources(ticker),
                    'macro':lambda:collectors.macro(ticker),'licensed_imports':lambda:collectors.imported(ticker,data),
                    'benchmark':lambda:benchmark('SPY')}
    source_results={}
    # Different providers concurrently; each provider controls its own sequential requests.
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(fn):name for name,fn in jobs.items()}
        for future in as_completed(futures):
            name=futures[future]
            try:
                received,issues=future.result();rows.extend(received);errors.extend(issues)
                source_results[name]={'records':len(received),'errors':issues,'status':'partial' if issues and received else 'failed' if issues else 'no_data' if not received else 'complete'}
            except Exception as exc:
                errors.append(name+': '+type(exc).__name__);source_results[name]={'records':0,'status':'failed','errors':[type(exc).__name__]}
            update('collect',f'{name}: {source_results[name]["records"]} records; {len(rows)} total')
    if collect is None:
        update('issuer_sources','Reading original issuer releases and investor-relations evidence')
        from .issuer import collect as issuer_collect
        received,issues=issuer_collect(ticker,rows,data)
        rows.extend(received);errors.extend(issues)
        source_results['issuer_ir']={'records':len(received),'errors':issues,'status':'partial' if issues else 'complete' if received else 'no_dated_releases'}
    def refresh():
        from .quarters import derive
        now=utcnow()
        stamped=annotate(derive(list({r['id']:r for r in rows}.values()),now),now)
        result=analyze(stamped,ticker)
        from .planner import plan
        result['investigation_plan']=plan(stamped,result,ticker)
        from .changes import compare
        result['since_previous_investigation']=compare(stamped,previous)
        return stamped,result
    stamped,analysis=refresh()
    synthesis={'summary':'Computed evidence scan. Deep investigation has not completed.','insights':[],
               'action':'investigate_further','action_reason':'Review the ranked findings and unresolved questions below.',
               'entry_conditions':[],'exit_conditions':[],'next_checks':[],'contradictions':[],'review_status':'not_run'}
    research_packets=[];proposal={};review={};model_limit=False
    if deep:
        for round_number in range(1,5):
            update('research',f'Research round {round_number}: follow material leads and challenge the strongest thesis')
            try:
                packet,usage=reasoner.research(ticker,analysis,round_number=round_number,previous=research_packets,runner=model)
                model_usage.append(usage);research_packets.append(packet);searches.extend(packet.get('queries_run',[]))
                new,rejected=reasoner.ingest_web(ticker,packet);rejections.extend(rejected)
                seen={r['url'] for r in rows};novel=[r for r in new if r['url'] not in seen]
                rows.extend(novel);relationships.extend(packet.get('relationships',[]))
                if round_number==1:
                    verified_urls={r['url'] for r in rows if r['kind']=='document'}
                    followed=set()
                    for relationship in packet.get('relationships',[])[:3]:
                        try:
                            peer=collectors.symbol(relationship.get('ticker',''))
                            if peer==ticker or peer in followed or relationship.get('evidence_url') not in verified_urls:continue
                            followed.add(peer);update('related_company',f'Cross-checking {peer}: {relationship.get("relationship","economic relationship")}')
                            peer_rows,issues=collectors.sec(peer,data)
                            rows.extend(peer_rows);errors.extend(issues)
                            source_results['related_'+peer]={'records':len(peer_rows),'status':'partial' if issues else 'complete','errors':issues}
                        except Exception as exc:errors.append('Related-company source: '+type(exc).__name__)
                stamped,analysis=refresh()
                # Stop only when the second targeted round produces no new verified source.
                update('research',f'Round {round_number}: {len(novel)} new verified documents; {len(rejected)} source checks failed')
                # Follow unresolved material questions while sources are still adding evidence.
                if round_number>=2 and (not novel or not packet.get('unresolved')):break
            except Exception as exc:
                errors.append('Research round '+str(round_number)+': '+type(exc).__name__+': '+str(exc)[:120])
                model_limit='quota or rate limit' in str(exc)
                break
        update('synthesize','Connecting evidence, expectations, contradictions and actionable conditions')
        try:
            if model_limit:raise RuntimeError('Model quota or rate limit reached; further model calls skipped for this run')
            proposal,usage=reasoner.synthesize(ticker,stamped,analysis,runner=model);model_usage.append(usage)
            atomic(root/'proposal.json',proposal)
            update('challenge','Checking the strongest counter-thesis and every load-bearing source')
            review,usage=reasoner.review(ticker,proposal,stamped,runner=model,analysis=analysis);model_usage.append(usage)
            synthesis=reasoner.finalize(proposal,review,stamped)
            if synthesis.get('review_status')!='model_challenged':
                update('revise','Correcting failed source checks and reconsidering the proposed action')
                atomic(root/'first_review.json',{'proposal':proposal,'review':review,'checks':synthesis.get('rejected_insights',[])})
                proposal,usage=reasoner.revise(ticker,proposal,{'review':review,'checks':synthesis.get('rejected_insights',[])},stamped,analysis,runner=model)
                model_usage.append(usage);atomic(root/'proposal.json',proposal)
                review={} # Never apply a previous review to changed claims.
                update('challenge','Reviewing the revised report against sources and computed results')
                review,usage=reasoner.review(ticker,proposal,stamped,runner=model,analysis=analysis)
                model_usage.append(usage);synthesis=reasoner.finalize(proposal,review,stamped)
        except Exception as exc:errors.append('Synthesis/review: '+type(exc).__name__+': '+str(exc)[:120])
    # Clock advances during investigation; reassess at publication, never renew underlying source timestamps.
    finished=utcnow();stamped,analysis=refresh()
    if proposal and review:synthesis=reasoner.finalize(proposal,review,stamped)
    snapshot={'schema_version':1,'run_id':run_id,'ticker':ticker,'started_at':started.isoformat(),'as_of':finished.isoformat(),
              'mode':'deep' if deep else 'evidence_scan','evidence':stamped,'analysis':analysis,'synthesis':synthesis,
              'sources':inventory(),'collection':source_results,'errors':errors,'source_rejections':rejections,
              'search':{'queries_run':list(dict.fromkeys(searches)),'rounds_completed':len(research_packets),
                        'relationships':relationships,'unresolved':list(dict.fromkeys(q for p in research_packets for q in p.get('unresolved',[]))),
                        'stop_reason':'research_limit_reached' if len(research_packets)==4 else 'questions_resolved_or_no_new_verified_evidence' if len(research_packets)>=2 else 'model_unavailable_or_incomplete' if deep else 'evidence_scan_requested',
                        'universal_exhaustion_claimed':False},
              'model_usage':model_usage,'stages':stages,'methodology_version':'investigator-1',
              'execution_authority':False}
    snapshot['snapshot_hash']=digest(snapshot)
    atomic(root/'report.json',snapshot);(root/'report.md').write_text(markdown(snapshot))
    atomic(root/'research.json',{'rounds':research_packets,'review':review})
    state='complete' if synthesis.get('review_status')=='model_challenged' else 'partial'
    atomic(root/'status.json',{'run_id':run_id,'ticker':ticker,'state':state,'started_at':started.isoformat(),
                             'updated_at':finished.isoformat(),'stage':'complete','detail':f'{len(stamped)} evidence records; {len(analysis["findings"])} computed findings; {len(synthesis["insights"])} challenged insights'})
    atomic(root.parent/'latest.json',{'run_id':run_id,'as_of':finished.isoformat(),'snapshot_hash':snapshot['snapshot_hash']})
    return snapshot


def markdown(report):
    s=report['synthesis'];a=report['analysis'];lookup={r['id']:r for r in report['evidence']}
    lines=[f'# {report["ticker"]} — investment intelligence',f'As of {report["as_of"]} · run {report["run_id"]}',
           '',f'**{s["action"].replace("_"," ").upper()}**',s['summary'],s['action_reason'],'','## Substantial insights']
    for i in s['insights']:
        lines += ['',f'### {i["title"]}',f'**Change:** {i["what_changed"]}',f'**Mechanism:** {i["mechanism"]}',
                  f'**Priced in:** {i["what_is_priced_in"]}',f'**Counterargument:** {i["counterargument"]}',
                  f'**Invalidation:** {i["invalidation"]}',f'**Horizon:** {i["horizon"]}']
        for e in i['evidence']:
            r=lookup.get(e['source_id'])
            if r:lines.append(f'- [{r["title"] or r["source"]}]({r["url"]}) — published {r.get("published_at")}; measured {r.get("period_end") or r.get("observed_at")}; {e["use"]}')
    lines+=['','## Reproducible findings']
    for f in a['findings']:lines += ['',f'**{f["title"]}** ({f["direction"]})',f['detail'],f'Sources: {", ".join(f["evidence_ids"])}']
    lines+=['','## Entry conditions']+['- '+x for x in s['entry_conditions']]
    lines+=['','## Exit / thesis invalidation']+['- '+x for x in s['exit_conditions']]
    lines+=['','## Open investigation questions']+['- '+x for x in s['next_checks']+report['search']['unresolved']]
    lines+=['','## Source and time coverage']
    for c in a['coverage']:lines.append(f'- {c["dimension"]}: {c["status"]}')
    lines+=['',f'Search stopping condition: {report["search"]["stop_reason"]}.',
            'Current and historical evidence are separated. Model challenge is not human approval. This report expresses research judgments and does not route orders.']
    return '\n'.join(lines)+'\n'


def load_run(data,ticker,run_id):
    ticker=collectors.symbol(ticker)
    if not str(run_id).isalnum() or len(run_id)>64:raise ValueError('Invalid report identifier')
    root=Path(data)/'intelligence'/'investigations'/ticker
    report=json.loads((root/run_id/'report.json').read_text());expected=report.pop('snapshot_hash')
    if digest(report)!=expected:raise ValueError('Investigation integrity mismatch')
    report['snapshot_hash']=expected
    return report


def load_latest(data,ticker):
    ticker=collectors.symbol(ticker);root=Path(data)/'intelligence'/'investigations'/ticker
    pointer=json.loads((root/'latest.json').read_text());run_id=pointer['run_id']
    if not run_id.isalnum() or len(run_id)>64:raise ValueError('Invalid report pointer')
    report=json.loads((root/run_id/'report.json').read_text());expected=report.pop('snapshot_hash')
    if digest(report)!=expected or pointer.get('snapshot_hash')!=expected:raise ValueError('Investigation integrity mismatch')
    report['snapshot_hash']=expected
    return report
