"""Frozen metadata for offline replay imports; no networking."""
import datetime as dt
from pathlib import Path
P=Path(__file__).resolve().parent
ROOT=P.parent
DATA=P/'data'
ASSETS=('DOGE',)
def ts(s):
    return int(dt.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp())
START=ts('2026-10-01T00:00:00Z')
END=ts('2026-10-12T04:30:00Z')
