"""Share request spacing across investigator processes using the same host account."""
import fcntl
import os
import time
from pathlib import Path


def reserve(path,interval=15,timeout=180,clock=time.time,sleep=time.sleep):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_CREAT|os.O_RDWR,0o600)
    with os.fdopen(fd,'r+') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        try:previous=float(handle.read() or 0)
        except ValueError:previous=0
        now=clock();wait=max(0,previous+interval-now)
        if wait>=timeout-10:raise RuntimeError('Research provider queue exceeds request time budget')
        # Reserve the next slot under the lock, then release before waiting.
        handle.seek(0);handle.truncate();handle.write(str(now+wait));handle.flush()
        fcntl.flock(handle,fcntl.LOCK_UN)
    if wait:sleep(wait)
    return wait
