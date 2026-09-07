"""Compare a local model on a retained evidence snapshot without pretending to run a new search."""
import argparse
import json
import os
import time
from pathlib import Path
import requests
from advisor.investigator import reasoner, ollama
from advisor.investigator.engine import atomic
from advisor.investigator.temporal import annotate, utcnow
from advisor.investigator.analysis import analyze
from advisor.investigator.planner import plan


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--model',required=True)
    p.add_argument('--provider',choices=('ollama','gemini'),default='ollama')
    p.add_argument('--port',type=int,choices=(11434,11435),default=11435)
    p.add_argument('--thinking',action='store_true')
    p.add_argument('--draft-only',action='store_true',help='Retain an unapproved draft for content diagnosis; never an acceptance result')
    p.add_argument('--resume-draft',type=Path,help='Review and revise a retained draft from this exact evidence snapshot and model')
    p.add_argument('--conservative-sampling',action='store_true',help='Evaluate low-variance sampling without a novelty penalty')
    p.add_argument('--timeout',type=int,choices=(900,1800),default=900)
    args=p.parse_args()
    os.environ['ADVISOR_RESEARCH_PROVIDER']=args.provider
    source=json.loads(args.report.read_text())
    from advisor.intelligence.contract import digest
    assert digest({k:v for k,v in source.items() if k!='snapshot_hash'})==source['snapshot_hash'],'Input snapshot integrity mismatch'
    rows=annotate(source['evidence'],utcnow());ticker=source['ticker']
    analysis=analyze(rows,ticker);analysis['investigation_plan']=plan(rows,analysis,ticker)
    for key in ('issuer_identity','original_sources'):
        analysis[key]=source['analysis'].get(key,{})
    calls=[]
    def post(url,**kwargs):
        kwargs['json']['options']['num_thread']=8
        kwargs['timeout']=(5,args.timeout)
        response=requests.post(f'http://127.0.0.1:{args.port}/api/chat',**kwargs)
        original=response.iter_lines
        def tracked(*a,**kw):
            path=args.output.parent/(args.output.stem+'-calls')/str(len(calls)+1)/'stream.jsonl'
            with path.open('wb') as stream:
                for line in original(*a,**kw):
                    if line:stream.write(line+b'\n');stream.flush()
                    yield line
        response.iter_lines=tracked
        return response
    def runner(prompt,schema,**kwargs):
        call=args.output.parent/(args.output.stem+'-calls')/str(len(calls)+1)
        atomic(call/'request.json',{'prompt':prompt,'schema':schema,'at':utcnow().isoformat()})
        started=time.monotonic()
        if args.provider=='gemini':
            from advisor.investigator import gemini,runtime
            config={**runtime.config(),'ADVISOR_RESEARCH_PROVIDER':'gemini','ADVISOR_RESEARCH_MODEL':args.model}
            value,usage=gemini.generate(prompt,schema,config,min(180,args.timeout))
        else:
            value,usage=ollama.generate(prompt,schema,{'ADVISOR_RESEARCH_MODEL':args.model,'_local_thinking':args.thinking,'ADVISOR_RESEARCH_SAMPLING':'conservative' if args.conservative_sampling else 'official'},args.timeout,post=post)
        usage.setdefault('elapsed_seconds',round(time.monotonic()-started,2))
        atomic(call/'response.json',{'value':value,'usage':usage});calls.append(usage)
        print(json.dumps({'ticker':ticker,'completed_model_call':len(calls),'elapsed_seconds':usage['elapsed_seconds']}),flush=True)
        return value,usage
    if args.resume_draft:
        draft=json.loads(args.resume_draft.read_text())
        if draft.get('evaluation_mode')!='unapproved_synthesis_draft' or draft.get('input_snapshot_hash')!=source['snapshot_hash'] or draft.get('model')!=args.model:
            raise ValueError('Draft provenance does not match this model and evidence snapshot')
        proposal=draft['proposal'];calls.extend(draft.get('model_usage',[]))
    else:
        proposal,_=reasoner.synthesize(ticker,rows,analysis,runner=runner)
    if args.draft_only:
        atomic(args.output,{'ticker':ticker,'evaluation_mode':'unapproved_synthesis_draft','input_snapshot_hash':source['snapshot_hash'],'model':args.model,'proposal':proposal,'model_usage':calls,'human_content_review_required':True,'integration_gate_passed':False})
        print(json.dumps({'ticker':ticker,'draft_saved':str(args.output),'approved':False}),flush=True)
        return
    review,_=reasoner.review(ticker,proposal,rows,runner=runner,analysis=analysis)
    result=reasoner.finalize(proposal,review,rows)
    if result['review_status']!='model_challenged':
        proposal,_=reasoner.revise(ticker,proposal,{'review':review,'checks':result['rejected_insights']},rows,analysis,runner=runner)
        review,_=reasoner.review(ticker,proposal,rows,runner=runner,analysis=analysis)
        result=reasoner.finalize(proposal,review,rows)
    output={'ticker':ticker,'evaluation_mode':'synthesis_replay_not_new_search','input_snapshot_hash':source['snapshot_hash'],
            'input_as_of':source['as_of'],'evaluated_at':utcnow().isoformat(),'model':args.model,'provider':args.provider,
            'synthesis':result,'review':review,'model_usage':calls,'human_content_review_required':True}
    atomic(args.output,output)
    print(json.dumps(output),flush=True)


if __name__=='__main__':main()
