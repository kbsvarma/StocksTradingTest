#!/usr/bin/env python3
"""
PRE-REGISTERED census of mechanical "first 15 minutes" strategies.
Window: enter and exit within 09:30-09:45 ET; flat by 09:45; no trades after.

Families (registered before any results were seen):
  A - Opening-drive continuation: dir = sign(09:30 -> 09:30+N return), N in {1,2,3,5};
      enter at 09:30+N close; exit 09:45 close. Variants: stop in {none, 1xATR(5d opening-15m range)}.
  B - Opening-drive fade: reverse of A, same cells.
  C - Gap continuation / fade: gap = 09:30 open vs prior session 16:00 close (15:59 bar close);
      buckets |gap| in {>0.2%, >0.5%, >1.0%}; enter 09:30 open; exit 09:45 close.
  D - Micro-ORB: range of first {3,5} min; enter on 1-min break of range high/low
      (long / short / both); exit 09:45 close; stop in {none, opposite side of range}.
  E - Overnight-range breakout: if 09:30 open outside full overnight range (18:00 prev ET -> 09:29),
      trade with the break at 09:30 open; exit 09:45 close.
  F - First-5-min extreme fade: z of 09:30->09:35 return vs trailing 60-session distribution;
      fade beyond +/- {1.5, 2.0}; enter 09:35 close; exit 09:45 close.

Primary census = ES + NQ (37 cells x 2 = 74 cells). MES runs the same grid but is
DIAGNOSTIC ONLY (cost scaling confirmation) and is excluded from the Bonferroni count.

Costs (applied per round turn, non-negotiable):
  ES : spread 1 tick each side = $12.50 x 2 + $2.50 comm = $27.50 RT
  NQ : $5.00 x 2 + $2.50 = $12.50 RT
  MES: $1.25 x 2 + $1.00 = $3.50 RT

FINDING gate (pre-registered): net>0 in BOTH split-halves AND |t|>=2.5 on net per-trade
PnL AND n>=200 AND Bonferroni-significant at 74 cells (p < 0.05/74 = 6.76e-4, |t| >= ~3.42).

Crash-resilient: each finished cell appended to cell_census_partial.jsonl (atomic tmp+rename
of the whole file after each append batch); completed cells skipped on restart.
"""
import json, os, sys, math
import numpy as np
import pandas as pd

BASE = '/Users/varmakammili/Documents/GitHub/StocksTradingTest'
OUT = os.path.join(BASE, 'spx_spread_bot/backtests/opening15_2026_07_03')
PARTIAL = os.path.join(OUT, 'cell_census_partial.jsonl')
FINAL = os.path.join(OUT, 'cell_census.csv')
ET = 'America/New_York'

SPEC = {
    'ES':  dict(pv=50.0,  tick=0.25, cost_rt=27.50,
                src=os.path.join(BASE, 'spx_spread_bot/backtests/futures_intraday/ES_1m.parquet')),
    'NQ':  dict(pv=20.0,  tick=0.25, cost_rt=12.50,
                src=os.path.join(BASE, 'spx_spread_bot/backtests/futures_intraday/NQ_1m.parquet')),
    'MES': dict(pv=5.0,   tick=0.25, cost_rt=3.50,
                src=os.path.join(BASE, 'mes_orb_bot/data/mes_1min.csv')),
}

# ---------------------------------------------------------------- data prep

def load_instrument(sym):
    spec = SPEC[sym]
    if spec['src'].endswith('.parquet'):
        df = pd.read_parquet(spec['src'])
        df.index = df.index.tz_convert(ET)
        has_id = 'instrument_id' in df.columns
    else:
        df = pd.read_csv(spec['src'])
        df.index = pd.to_datetime(df.pop('ts_event'), utc=True).dt.tz_convert(ET)
        df['instrument_id'] = 0
        has_id = False
    df = df.sort_index()
    # session date: bars from 18:00 ET belong to next calendar day
    sess = (df.index + pd.Timedelta(hours=6)).date
    df = df.assign(session=sess)
    return df, has_id


def build_sessions(sym):
    """Return dict of per-session numpy caches + exclusion stats."""
    df, has_id = load_instrument(sym)
    minutes = df.index.hour * 60 + df.index.minute
    df = df.assign(minute=minutes)
    stats = dict(total_candidate=0, excluded_missing_open=0, excluded_halfday=0,
                 excluded_weekend=0, roll_days=0)
    sessions = []
    for d, g in df.groupby('session'):
        if pd.Timestamp(d).weekday() >= 5:
            stats['excluded_weekend'] += 1
            continue
        stats['total_candidate'] += 1
        m = g['minute'].values
        # opening window bars: minutes 570 (09:30) .. 584 (09:44)
        win = g[(m >= 570) & (m <= 584)]
        wm = win['minute'].values
        if 570 not in wm or 584 not in wm or len(wm) < 14:
            stats['excluded_missing_open'] += 1
            continue
        # half-day / holiday detection: require a 15:59 bar in the same session
        if not ((m == 959).any()):
            stats['excluded_halfday'] += 1
            continue
        # dense 15-bar arrays (ffill interior missing bar if exactly one is missing)
        idx = wm - 570
        o = np.full(15, np.nan); h = np.full(15, np.nan)
        l = np.full(15, np.nan); c = np.full(15, np.nan)
        o[idx] = win['open'].values; h[idx] = win['high'].values
        l[idx] = win['low'].values;  c[idx] = win['close'].values
        for arr in (o, h, l, c):
            for i in range(1, 15):
                if np.isnan(arr[i]):
                    arr[i] = c[i-1] if not np.isnan(c[i-1]) else arr[i-1]
        on = g[(m >= 1080) | (m < 570)]  # 18:00+ prev evening through 09:29
        close_1559 = g.loc[g['minute'] == 959, 'close'].values[-1]
        iid_open = int(g.loc[g['minute'] == 570, 'instrument_id'].values[0]) if has_id else 0
        sessions.append(dict(
            date=pd.Timestamp(d), o=o, h=h, l=l, c=c,
            on_high=float(on['high'].max()) if len(on) else np.nan,
            on_low=float(on['low'].min()) if len(on) else np.nan,
            close_1559=float(close_1559), iid=iid_open,
        ))
    sessions.sort(key=lambda s: s['date'])
    # derived cross-session fields
    r15 = [s['h'][:15].max() - s['l'][:15].min() for s in sessions]
    r5s = [(s['c'][4] - s['o'][0]) / s['o'][0] for s in sessions]
    for i, s in enumerate(sessions):
        s['prior_close'] = sessions[i-1]['close_1559'] if i > 0 else np.nan
        s['roll'] = (i > 0 and s['iid'] != sessions[i-1]['iid'])
        if s['roll']:
            stats['roll_days'] += 1
        s['atr5'] = float(np.mean(r15[max(0, i-5):i])) if i >= 5 else np.nan
        hist = np.array(r5s[max(0, i-60):i])
        if len(hist) >= 40 and np.std(hist) > 0:
            s['z5'] = (r5s[i] - hist.mean()) / hist.std(ddof=1)
        else:
            s['z5'] = np.nan
    return sessions, stats

# ---------------------------------------------------------------- trade sims

def sim_stop_path(s, dirn, entry_px, start_bar, stop_px):
    """Walk bars start_bar..14; return exit px (stop or 09:45 close). Stop checked
    from start_bar onward; conservative gap-through fill at bar open."""
    for i in range(start_bar, 15):
        if dirn > 0 and s['l'][i] <= stop_px:
            return min(s['o'][i], stop_px) if s['o'][i] <= stop_px else stop_px
        if dirn < 0 and s['h'][i] >= stop_px:
            return max(s['o'][i], stop_px) if s['o'][i] >= stop_px else stop_px
    return s['c'][14]


def trades_family_AB(sessions, fade, N, use_stop):
    out = []
    for k, s in enumerate(sessions):
        ret = s['c'][N-1] - s['o'][0]
        if ret == 0 or (use_stop and np.isnan(s['atr5'])):
            continue
        dirn = int(np.sign(ret)) * (-1 if fade else 1)
        entry = s['c'][N-1]
        if use_stop:
            stop = entry - dirn * s['atr5']
            exit_px = sim_stop_path(s, dirn, entry, N, stop)
        else:
            exit_px = s['c'][14]
        out.append((k, dirn, entry, exit_px))
    return out


def trades_family_C(sessions, fade, thresh):
    out = []
    for k, s in enumerate(sessions):
        if np.isnan(s['prior_close']) or s['roll']:
            continue
        gap = s['o'][0] / s['prior_close'] - 1.0
        if abs(gap) <= thresh or gap == 0:
            continue
        dirn = int(np.sign(gap)) * (-1 if fade else 1)
        out.append((k, dirn, s['o'][0], s['c'][14]))
    return out


def trades_family_D(sessions, K, side, use_stop):
    out = []
    for k, s in enumerate(sessions):
        rh = s['h'][:K].max(); rl = s['l'][:K].min()
        trig = None
        for i in range(K, 15):
            up = s['h'][i] > rh; dn = s['l'][i] < rl
            if side == 'long' and up:
                trig = (i, 1, max(s['o'][i], rh)); break
            if side == 'short' and dn:
                trig = (i, -1, min(s['o'][i], rl)); break
            if side == 'both':
                if up and dn:
                    trig = 'ambig'; break
                if up:
                    trig = (i, 1, max(s['o'][i], rh)); break
                if dn:
                    trig = (i, -1, min(s['o'][i], rl)); break
        if trig is None or trig == 'ambig':
            continue
        i, dirn, entry = trig
        if i == 14:
            continue  # no time left before 09:45 exit
        if use_stop:
            stop = rl if dirn > 0 else rh
            exit_px = sim_stop_path(s, dirn, entry, i + 1, stop)
        else:
            exit_px = s['c'][14]
        out.append((k, dirn, entry, exit_px))
    return out


def trades_family_E(sessions):
    out = []
    for k, s in enumerate(sessions):
        if np.isnan(s['on_high']) or s['roll']:
            continue
        if s['o'][0] > s['on_high']:
            dirn = 1
        elif s['o'][0] < s['on_low']:
            dirn = -1
        else:
            continue
        out.append((k, dirn, s['o'][0], s['c'][14]))
    return out


def trades_family_F(sessions, thr):
    out = []
    for k, s in enumerate(sessions):
        z = s['z5']
        if np.isnan(z) or abs(z) < thr:
            continue
        dirn = -1 if z > 0 else 1
        out.append((k, dirn, s['c'][4], s['c'][14]))
    return out

# ---------------------------------------------------------------- cell stats

def summarize(trades, sessions, spec, vix_prior):
    n = len(trades)
    if n == 0:
        return dict(n=0)
    ks = np.array([t[0] for t in trades])
    dirs = np.array([t[1] for t in trades], dtype=float)
    gross = np.array([(t[3] - t[2]) * t[1] * spec['pv'] for t in trades])
    net = gross - spec['cost_rt']
    half = len(sessions) // 2
    h1 = ks < half
    t_stat = float(net.mean() / (net.std(ddof=1) / math.sqrt(n))) if n > 1 and net.std(ddof=1) > 0 else np.nan
    vix = np.array([vix_prior[k] for k in ks])
    res = dict(
        n=int(n),
        gross_total=round(float(gross.sum()), 2),
        net_total=round(float(net.sum()), 2),
        net_per_trade=round(float(net.mean()), 2),
        t_net=round(t_stat, 3) if not np.isnan(t_stat) else np.nan,
        half1_n=int(h1.sum()), half1_net=round(float(net[h1].sum()), 2),
        half2_n=int((~h1).sum()), half2_net=round(float(net[~h1].sum()), 2),
        long_n=int((dirs > 0).sum()), long_net=round(float(net[dirs > 0].sum()), 2),
        short_n=int((dirs < 0).sum()), short_net=round(float(net[dirs < 0].sum()), 2),
        vix_lt20_n=int(((vix >= 0) & (vix < 20)).sum()),
        vix_lt20_net=round(float(net[(vix >= 0) & (vix < 20)].sum()), 2),
        vix_ge20_n=int((vix >= 20).sum()), vix_ge20_net=round(float(net[vix >= 20].sum()), 2),
    )
    return res

# ---------------------------------------------------------------- grid

def enumerate_cells():
    cells = []
    for N in (1, 2, 3, 5):
        for stop in (False, True):
            cells.append(('A', f'A_cont_N{N}_stop{int(stop)}',
                          dict(fn='AB', fade=False, N=N, stop=stop)))
            cells.append(('B', f'B_fade_N{N}_stop{int(stop)}',
                          dict(fn='AB', fade=True, N=N, stop=stop)))
    for th, lbl in ((0.002, '0.2'), (0.005, '0.5'), (0.010, '1.0')):
        cells.append(('C', f'C_cont_gap{lbl}pct', dict(fn='C', fade=False, thresh=th)))
        cells.append(('C', f'C_fade_gap{lbl}pct', dict(fn='C', fade=True, thresh=th)))
    for K in (3, 5):
        for side in ('long', 'short', 'both'):
            for stop in (False, True):
                cells.append(('D', f'D_orb{K}m_{side}_stop{int(stop)}',
                              dict(fn='D', K=K, side=side, stop=stop)))
    cells.append(('E', 'E_on_range_break', dict(fn='E')))
    for thr in (1.5, 2.0):
        cells.append(('F', f'F_z5_fade_{thr}', dict(fn='F', thr=thr)))
    return cells


def run_cell(params, sessions):
    fn = params['fn']
    if fn == 'AB':
        return trades_family_AB(sessions, params['fade'], params['N'], params['stop'])
    if fn == 'C':
        return trades_family_C(sessions, params['fade'], params['thresh'])
    if fn == 'D':
        return trades_family_D(sessions, params['K'], params['side'], params['stop'])
    if fn == 'E':
        return trades_family_E(sessions)
    if fn == 'F':
        return trades_family_F(sessions, params['thr'])
    raise ValueError(fn)


def main():
    os.makedirs(OUT, exist_ok=True)
    done = set()
    rows = []
    if os.path.exists(PARTIAL):
        with open(PARTIAL) as f:
            for line in f:
                r = json.loads(line)
                done.add((r['instrument'], r['cell_id']))
                rows.append(r)
        print(f'resume: {len(done)} cells already complete')

    vixdf = pd.read_csv(os.path.join(BASE, 'spx_spread_bot/backtests/ten_year/spx_vix_daily.csv'),
                        parse_dates=['date'])
    vix_series = vixdf.set_index('date')['vix_close'].sort_index()

    cells = enumerate_cells()
    coverage = {}
    for sym in ('ES', 'NQ', 'MES'):
        pending = [c for c in cells if (sym, c[1]) not in done]
        print(f'== {sym}: {len(pending)} cells to run')
        sessions, stats = build_sessions(sym)
        coverage[sym] = dict(stats, n_sessions=len(sessions),
                             first=str(sessions[0]['date'].date()),
                             last=str(sessions[-1]['date'].date()))
        print(f'   sessions={len(sessions)} {coverage[sym]["first"]}..{coverage[sym]["last"]} '
              f'excl_missing={stats["excluded_missing_open"]} excl_halfday={stats["excluded_halfday"]} '
              f'rolls={stats["roll_days"]}')
        # prior-close VIX aligned to each session (asof previous available VIX date)
        dates = pd.DatetimeIndex([s['date'] for s in sessions])
        vix_prior = vix_series.reindex(
            vix_series.index.union(dates)).ffill().shift(1).reindex(dates).values
        vix_prior = np.where(np.isnan(vix_prior), -1, vix_prior)  # -1 => excluded from both VIX buckets
        for fam, cid, params in pending:
            trades = run_cell(params, sessions)
            # mask trades with missing VIX for the VIX split only (handled inside summarize via vix value)
            summ = summarize(trades, sessions, SPEC[sym], vix_prior)
            row = dict(instrument=sym, family=fam, cell_id=cid,
                       params=json.dumps({k: v for k, v in params.items() if k != 'fn'}),
                       primary=(sym in ('ES', 'NQ')), **summ)
            rows.append(row)
            with open(PARTIAL, 'a') as f:
                f.write(json.dumps(row) + '\n')
    # final CSV (atomic)
    dfout = pd.DataFrame(rows)
    tmp = FINAL + '.tmp'
    dfout.to_csv(tmp, index=False)
    os.replace(tmp, FINAL)
    with open(os.path.join(OUT, 'coverage.json.tmp'), 'w') as f:
        json.dump(coverage, f, indent=2, default=str)
    os.replace(os.path.join(OUT, 'coverage.json.tmp'), os.path.join(OUT, 'coverage.json'))
    print(f'wrote {FINAL} with {len(dfout)} rows')

    # gate evaluation on PRIMARY cells
    prim = dfout[dfout.primary & (dfout.n > 0)].copy()
    n_cells = len(dfout[dfout.primary])
    from scipy import stats as st
    bonf_p = 0.05 / n_cells
    print(f'primary cells: {n_cells}; Bonferroni p<{bonf_p:.2e}')
    prim['p'] = 2 * st.t.sf(np.abs(prim.t_net), prim.n - 1)
    gate = prim[(prim.net_total > 0) & (prim.half1_net > 0) & (prim.half2_net > 0) &
                (prim.t_net >= 2.5) & (prim.n >= 200) & (prim.p < bonf_p)]
    print('GATE PASSERS:', len(gate))
    if len(gate):
        print(gate[['instrument', 'cell_id', 'n', 'net_total', 't_net', 'half1_net', 'half2_net']])
    near = prim[(prim.t_net > 1.5) & (prim.net_total > 0)].sort_values('t_net', ascending=False)
    print('near-misses (t>1.5, net>0):')
    print(near[['instrument', 'cell_id', 'n', 'net_total', 'net_per_trade', 't_net',
                'half1_net', 'half2_net']].to_string())


if __name__ == '__main__':
    main()
