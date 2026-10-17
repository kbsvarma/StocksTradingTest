"""Isolated public GET research, cached raw receipts and shared pacing."""
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import fcntl
import requests

P=Path(__file__).resolve().parent
BASE='https://external-api.kalshi.com/trade-api/v2'
RAW=P/'raw';RAW.mkdir(exist_ok=True)
START=int(dt.datetime(2026,9,14,4,tzinfo=dt.timezone.utc).timestamp())
END=int(dt.datetime(2026,10,14,16,tzinfo=dt.timezone.utc).timestamp())
EXT=int(dt.datetime(2026,10,12,4,tzinfo=dt.timezone.utc).timestamp())
def ts(s):return int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
def read(p):
    p=Path(p)
    if p.suffix=='.gz':
        with gzip.open(p,'rt') as f:return json.load(f)
    return json.loads(p.read_text())
def save(p,obj):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_name(p.name+'.'+str(os.getpid())+'.'+str(threading.get_ident())+'.tmp')
    if p.suffix=='.gz':
        with gzip.open(tmp,'wt') as f:json.dump(obj,f,allow_nan=False)
    else:tmp.write_text(json.dumps(obj,indent=2,allow_nan=False)+'\n')
    tmp.replace(p)
def get(path,params=None,cache=True):
    assert path.startswith(('/markets','/series','/events','/live_data','/historical'))
    url=requests.Request('GET',BASE+path,params=params).prepare().url
    dest=RAW/(hashlib.sha256(url.encode()).hexdigest()+'.json.gz')
    if cache and dest.exists():return read(dest)['response']
    for attempt in range(5):
        with (P/'request_clock').open('a+') as f:
            fcntl.flock(f,fcntl.LOCK_EX);f.seek(0);last=float(f.read() or 0)
            time.sleep(max(0,last+1-time.time()));f.seek(0);f.truncate();f.write(str(time.time()));f.flush()
        try:
            r=requests.get(url,timeout=25)
            if r.status_code in (400,404):
                save(dest,dict(url=url,retrieved_ts=time.time(),status=r.status_code,response=r.json()))
                raise ValueError(str(r.status_code)+' '+url)
            if r.status_code==429:time.sleep(30)
            r.raise_for_status();x=r.json();save(dest,dict(url=url,retrieved_ts=time.time(),status=r.status_code,response=x));return x
        except requests.RequestException:
            if attempt==4:raise
            time.sleep(2**attempt)
def paged(path,params,key):
    rows=[];cursor='';seen=set()
    while True:
        x=get(path,{**params,**({'cursor':cursor} if cursor else {})});rows.extend(x.get(key,[]));cursor=x.get('cursor')
        if not cursor:return rows
        assert cursor not in seen;seen.add(cursor)
