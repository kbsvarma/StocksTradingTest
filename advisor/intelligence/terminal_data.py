"""Local chart and security context; no UI-triggered provider requests."""
import hashlib
import json
from io import BytesIO
from pathlib import Path
import pandas as pd


def price_history(data, ticker, sessions=126):
    root = Path(data)/'research'/'panels'
    if (root/'CURRENT').exists():
        build = (root/'CURRENT').read_text().strip()
        if not build or Path(build).name != build: raise ValueError('Invalid price-panel pointer')
        root = root/'builds'/build
    meta = json.loads((root/'meta.json').read_text()) if (root/'meta.json').exists() else {}
    series = {}
    for field in ('close', 'open', 'high', 'low', 'volume'):
        path = root/f'{field}.parquet'
        if not path.exists(): continue
        raw = path.read_bytes()
        expected = meta.get('sha256', {}).get(path.name)
        if expected and hashlib.sha256(raw).hexdigest() != expected: raise ValueError('Chart source integrity mismatch')
        try:
            panel = pd.read_parquet(BytesIO(raw), columns=[ticker])
        except (KeyError, ValueError):
            continue
        series[field] = panel[ticker].tail(sessions)
    frame = pd.DataFrame(series)
    if 'close' in frame: frame = frame[frame['close'].notna()]
    return frame, meta


def parse_command(text, known_symbols):
    parts = text.upper().strip().split()
    functions = {'DESK', 'CALLS', 'SEC', 'EVID', 'EVENTS', 'PERF', 'LAB', 'OPS', 'HELP', 'INT', 'WATCH'}
    if not parts: return None, None, None
    if len(parts) == 1 and parts[0] in functions: return parts[0], None, None
    if len(parts) <= 2 and (len(parts) == 1 or parts[1] in {'INT', 'INVESTIGATE'}):
        from advisor.investigator.collectors import symbol
        try:
            ticker = symbol(parts[0])
            if ticker not in known_symbols or len(parts) == 2: return 'INT', ticker, None
        except ValueError: pass
    if parts[0] in known_symbols and len(parts) <= 2:
        page = parts[1] if len(parts) == 2 else 'SEC'
        if page in {'DES', 'GP'}: page = 'SEC'
        if page in {'SEC', 'EVID', 'CALLS'}: return page, parts[0], None
    return None, None, 'Use a covered ticker, TICKER DES, TICKER EVID, TICKER INT for on-demand investigation, or a workspace command.'
