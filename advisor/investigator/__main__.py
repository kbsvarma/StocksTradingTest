"""python -m advisor.investigator NVDA [--data-dir ...] [--scan-only]."""
import argparse
import json
import multiprocessing
import os
import signal
import uuid
from pathlib import Path
from .engine import run, atomic
from .collectors import symbol


from .supervisor import _execute, _failed


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ticker',type=symbol)
    parser.add_argument('--data-dir',type=Path,default=Path(__file__).resolve().parents[1]/'data')
    parser.add_argument('--scan-only',action='store_true',default=False,help='Collect sources and calculate findings without an assistant account')
    parser.add_argument('--run-id',default=uuid.uuid4().hex)
    args=parser.parse_args()
    if not args.run_id.isalnum() or len(args.run_id)>64:parser.error('Invalid run identifier')
    process=multiprocessing.get_context('spawn').Process(target=_execute,args=(args,))
    process.start()
    try:
        from .runtime import config, provider
        job_budget=3600 if provider(config())=='ollama' else 1200
        process.join(job_budget)
        if process.is_alive():
            _failed(args,f'Investigation exceeded its {job_budget//60}-minute job budget')
            os.killpg(process.pid,signal.SIGTERM);process.join(3)
            if process.is_alive():os.killpg(process.pid,signal.SIGKILL);process.join()
            return 124
        if process.exitcode:
            path=args.data_dir/'intelligence'/'investigations'/args.ticker/args.run_id/'status.json'
            try:state=json.loads(path.read_text()).get('state')
            except (OSError,ValueError):state=None
            if state!='failed':_failed(args,'Investigation worker exited with code '+str(process.exitcode))
        return process.exitcode or 0
    except KeyboardInterrupt:
        _failed(args,'Investigation cancelled by operator')
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        process.join(3)
        if process.is_alive():os.killpg(process.pid,signal.SIGKILL);process.join()
        return 130

if __name__=='__main__':raise SystemExit(main())
