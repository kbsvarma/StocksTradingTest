"""Importable process target for spawn-based investigation isolation."""
import os
import json
from .engine import run, atomic

def _failed(args,detail):
    from .temporal import utcnow
    atomic(args.data_dir/'intelligence'/'investigations'/args.ticker/args.run_id/'status.json',
           {'ticker':args.ticker,'run_id':args.run_id,'state':'failed','updated_at':utcnow().isoformat(),
            'stage':'failed','detail':detail})


def _execute(args):
    # A separate process group lets the supervisor stop model subprocesses and
    # blocked provider threads together without touching the caller's terminal.
    os.setsid()
    try:
        report=run(args.ticker,args.data_dir,deep=not args.scan_only,run_id=args.run_id,
                   progress=lambda p:print(json.dumps(p),flush=True))
        print(json.dumps({'ticker':report['ticker'],'run_id':report['run_id'],'action':report['synthesis']['action'],
                          'evidence_records':len(report['evidence']),'findings':len(report['analysis']['findings']),
                          'insights':len(report['synthesis']['insights']),'errors':report['errors']}),flush=True)
    except Exception as exc:
        _failed(args,type(exc).__name__+': '+str(exc)[:200])
        raise

