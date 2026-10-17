"""Run real research evaluations and retain evidence, failures and timing.

This is an integration/grounding gate, not a claim of investment performance.
"""
import argparse
import json
from pathlib import Path
import time
from advisor.investigator.engine import run, atomic
from advisor.investigator.reasoner import validate_synthesis
from advisor.investigator.runtime import status


def assess(report):
    synthesis=report['synthesis']
    _,bad=validate_synthesis(synthesis,report['evidence'])
    checks={
        'review_completed':synthesis.get('review_status')=='model_challenged',
        'substantial_insights_present':bool(synthesis.get('insights')),
        'exact_citations_and_time_basis':bool(synthesis.get('insights')) and not bad,
        'search_executed':len(report['search'].get('queries_run',[]))>=2,
        'counterarguments_and_falsifiers':bool(synthesis.get('insights')) and all(
            i.get('counterargument') and i.get('invalidation') and i.get('mechanism') for i in synthesis.get('insights',[])),
        'no_execution_authority':report.get('execution_authority') is False,
    }
    return {'ticker':report['ticker'],'run_id':report['run_id'],'checks':checks,
            'integration_gate_passed':all(checks.values()),'action':synthesis['action'],
            'summary':synthesis['summary'],'action_reason':synthesis['action_reason'],
            'insight_titles':[i['title'] for i in synthesis['insights']],
            'errors':report['errors'],'human_content_review_required':True}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tickers',nargs='+',default=['NVDA','MSFT','AAPL','GOOGL','JPM','JNJ'])
    p.add_argument('--data-dir',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    args=p.parse_args();results=[]
    for ticker in args.tickers:
        started=time.monotonic()
        try:
            report=run(ticker,args.data_dir,deep=True,progress=lambda item:print(json.dumps({'ticker':ticker,**item}),flush=True))
            result=assess(report)
        except Exception as exc:
            result={'ticker':ticker,'integration_gate_passed':False,'error':type(exc).__name__+': '+str(exc)}
        result['elapsed_seconds']=round(time.monotonic()-started,2);results.append(result)
        atomic(args.output,{'provider':status(),'results':results,'performance_or_profitability_validated':False})
        print(json.dumps(result),flush=True)


if __name__=='__main__':main()
