"""Conservative daily-bar simulation of a published entry band.

Separate from signal returns. Intraday ordering is never invented. These are
modelled executions on adjusted bars, not broker fills.
"""
from __future__ import annotations
from advisor.suggestion_policy import finite


def simulate(pick, bars, *, matured):
    required = ('open', 'high', 'low', 'close')
    if any(k not in bars for k in required) or bars[list(required)].isna().any().any():
        return {'outcome': 'simulation_unavailable', 'status': 'resolved' if matured else 'open',
                'reason': 'Complete OHLC required; missing opens cannot establish gap fills',
                'calibration_eligible': False, 'win': None, 'return_pct': None}
    long = pick['direction'] == 'long'
    lo, hi, stop, target = (pick[k] for k in ('entry_low', 'entry_high', 'stop', 'target'))
    entry = exit_px = entry_date = exit_date = None
    outcome = None
    for day, bar in bars.iterrows():
        o, h, l, c = (float(bar[k]) for k in required)
        if not all(finite(v) and v > 0 for v in (o, h, l, c)) or not l <= min(o, c) <= max(o, c) <= h:
            return {'outcome': 'simulation_unavailable', 'status': 'open',
                    'reason': 'Invalid OHLC', 'calibration_eligible': False,
                    'win': None, 'return_pct': None}
        opening_entry = False
        if entry is None:
            invalid_open = (o <= stop or o >= target) if long else (o >= stop or o <= target)
            if invalid_open:
                outcome, exit_date = 'not_entered', str(day.date())
                break
            if lo <= o <= hi:
                entry, opening_entry = o, True
            elif l <= hi and h >= lo:
                entry = hi if o > hi else lo
            else:
                continue
            entry_date = str(day.date())
        stp = l <= stop if long else h >= stop
        tgt = h >= target if long else l <= target
        # Before checking the range, gaps on a subsequent bar have known order.
        if str(day.date()) != entry_date:
            if (o <= stop) if long else (o >= stop):
                outcome, exit_px = 'stop', o
            elif (o >= target) if long else (o <= target):
                outcome, exit_px = 'target', o
        if outcome is None:
            if (stp and tgt) or (str(day.date()) == entry_date and not opening_entry and (stp or tgt)):
                outcome = 'ambiguous'
            elif stp:
                outcome, exit_px = 'stop', stop
            elif tgt:
                outcome, exit_px = 'target', target
        if outcome:
            exit_date = str(day.date())
            break
    if not outcome and not matured:
        return {'status': 'open', 'entry_simulated_px': entry, 'entry_simulated_date': entry_date}
    if not outcome:
        outcome = 'expired' if entry is not None else 'not_entered'
        exit_px = float(bars.iloc[-1]['close']) if entry is not None else None
        exit_date = str(bars.index[-1].date()) if len(bars) else None
    sign = 1 if long else -1
    gross = sign * (exit_px / entry - 1) * 100 if entry and exit_px else None
    cost = (pick.get('cost') or {}).get('round_trip_bps')
    cost_ok = finite(cost) and cost > 0
    net = gross - cost / 100 if gross is not None and cost_ok else None
    # Short borrow is currently not captured; never call its result cost-complete.
    if not long:
        net = None
    risk = abs(entry - stop) if entry is not None else None
    return {'status': 'resolved', 'outcome': outcome,
            'entry_simulated_px': entry, 'entry_simulated_date': entry_date,
            'exit_px': exit_px, 'exit_date': exit_date,
            'gross_return_pct': round(gross, 4) if gross is not None else None,
            'return_pct': round(net, 4) if net is not None else None,
            'r_multiple': round(net / (risk / entry * 100), 4) if net is not None and risk else None,
            'win': int(net > 0) if net is not None else None,
            'calibration_eligible': net is not None and outcome not in ('ambiguous', 'not_entered'),
            'cost_complete': net is not None,
            'simulation_note': 'Adjusted daily OHLC; modelled spread/impact; not realized fills'}
