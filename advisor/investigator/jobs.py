"""Explicit user-triggered background jobs; rendering never fetches providers."""
import fcntl
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from .collectors import symbol
from .engine import atomic
from .temporal import utcnow


def start(data,ticker,principal,*,deep=False):
    principal.require('propose');ticker=symbol(ticker)
    data=Path(data).resolve();root=data/'intelligence'/'investigations'/ticker;root.mkdir(parents=True,exist_ok=True)
    with (root/'launch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        active=root/'active.json'
        if active.exists():
            previous=json.loads(active.read_text());status=read_status(data,ticker,previous['run_id'])
            if status.get('state') in {'queued','running'}:return previous
        run_id=uuid.uuid4().hex;jobroot=root/run_id;jobroot.mkdir()
        job={'run_id':run_id,'ticker':ticker,'requested_by':principal.user,'tenant':principal.tenant,'started_at':utcnow().isoformat()}
        atomic(jobroot/'status.json',{**job,'state':'queued','updated_at':utcnow().isoformat(),'stage':'queued','detail':'Starting investigator'})
        cmd=[sys.executable,'-m','advisor.investigator',ticker,'--data-dir',str(data),'--run-id',run_id]
        if not deep:cmd+=['--scan-only']
        with (jobroot/'job.log').open('w') as log:
            process=subprocess.Popen(cmd,cwd=Path(__file__).resolve().parents[2],stdout=log,stderr=log,start_new_session=True)
        job['pid']=process.pid;atomic(active,job)
        return job


def read_status(data,ticker,run_id):
    ticker=symbol(ticker)
    if not str(run_id).isalnum() or len(run_id)>64:raise ValueError('Invalid run id')
    path=Path(data)/'intelligence'/'investigations'/ticker/run_id/'status.json'
    try:status=json.loads(path.read_text())
    except (OSError,ValueError):return {'state':'missing','detail':'Job status unavailable'}
    from advisor.intelligence.contract import timestamp
    if status.get('state') in {'queued','running'}:
        if (utcnow()-timestamp(status['updated_at'])).total_seconds()>1250:
            status.update(state='failed',detail='Job stopped updating; start a new investigation')
    return status
