from pathlib import Path
import numpy as np,pandas as pd
from numba import njit
P=Path(__file__).resolve().parent;D=P/'data';O=P/'results';O.mkdir(exist_ok=True)
from build_futures import SPECS

def evaluate(d):
 # One observation per actual execution day; pooled studies include zero-position days.
 p=d['net'].cumsum();y=d.net.groupby(d.index.year).sum();span=(d.index[-1]-d.index[0]).total_seconds()/86400/365.25;ann=len(d)/span if span>0 else 252
 return dict(net=d.net.sum(),gross=d.gross.sum(),fees=d.fees.sum(),slippage=d.slippage.sum(),dd=(p-p.cummax().clip(lower=0)).min(),sharpe=d.net.mean()/d.net.std()*np.sqrt(ann) if d.net.std()>0 else np.nan,sd=d.net.std(),tail5=d.net[d.net<=d.net.quantile(.05)].mean(),worst_year=y.min(),days=len(d),active=int((d.activity>0).sum()),**{'y'+str(k):v for k,v in y.items()})

def partitions(d):
 out={}
 for label,lo,hi in [('development','2018','2023'),('validation','2023','2025'),('final','2025','2027'),('all','2018','2027')]:
  q=d[(d.index>=lo)&(d.index<hi)]
  if len(q):out[label]=evaluate(q)
 return out

def feespec(sym,case):
 mult,tick,_=SPECS[sym];fee=3.5 if sym in ['ES','NQ','YM'] else 7.;k=1 if case=='base' else 2
 return fee*k,tick*mult*k

def combine(books):
 # Common observed dates, preserving the fixed market basket.
 ix=next(iter(books.values())).index
 for q in books.values():ix=ix.intersection(q.index)
 return sum(q.reindex(ix) for q in books.values())
