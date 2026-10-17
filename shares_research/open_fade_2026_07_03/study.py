#!/usr/bin/env python3
"""Open-fade descriptive + viability study.

Claim under test: "most stocks that open, fall within the first 15 minutes"
and the viability of shorting at 09:30, covering 09:45-09:50, at retail.

Data: Yahoo 5-min bars, last 30 sessions (one request/ticker per repo budget;
Yahoo caps 1m at 8d/request so 1m was impossible within budget — 5m gives the
exact 09:30 open, 09:45 and 09:50 prices needed).

COST MODEL (pre-registered, conservative-floor):
 - half-spread per side: 3 bps for mega-caps (top 30 of universe by June-2026
   dollar volume), 5 bps for all others. Roundtrip = 2 sides.
 - commission $0.005/share x 2 sides, converted to bps at the entry price.
 - No borrow fees, no locate, no SSR, no halts modeled (all would go AGAINST
   the short — stated in caveats).
"""
import os
import numpy as np
import pandas as pd

BASE = '/Users/varmakammili/Documents/GitHub/StocksTradingTest/shares_research/open_fade_2026_07_03'
CACHE = os.path.join(BASE, 'pull_cache')

uni = pd.read_csv(os.path.join(BASE, 'universe.csv'))
mega = set(uni['ticker'].head(30))
COMM_PER_SHARE = 0.005

rows = []
for tk in uni['ticker']:
    fp = os.path.join(CACHE, f'{tk}.parquet')
    if not os.path.exists(fp):
        continue
    df = pd.read_parquet(fp)
    if df.empty:
        continue
    df = df.tz_convert('America/New_York') if df.index.tz else df.tz_localize('America/New_York')
    df['date'] = df.index.date
    prior_close = None
    prior_prior_close = None
    for d, g in df.groupby('date'):
        g = g.between_time('09:30', '16:00')
        day_close = g['Close'].iloc[-1] if len(g) else np.nan
        t = g.index.strftime('%H:%M')
        b930 = g[t == '09:30']
        b940 = g[t == '09:40']   # closes at 09:45
        b945 = g[t == '09:45']   # closes at 09:50
        if len(b930) and len(b940) and prior_close is not None and np.isfinite(prior_close):
            o = float(b930['Open'].iloc[0])
            p945 = float(b940['Close'].iloc[0])
            p950 = float(b945['Close'].iloc[0]) if len(b945) else np.nan
            r45 = p945 / o - 1.0
            r50 = p950 / o - 1.0 if np.isfinite(p950) else np.nan
            gap = o / prior_close - 1.0
            prevret = (prior_close / prior_prior_close - 1.0
                       if (prior_prior_close is not None and np.isfinite(prior_prior_close))
                       else np.nan)
            half_bps = 3.0 if tk in mega else 5.0
            comm_bps = 2 * COMM_PER_SHARE / o * 1e4
            cost_bps = 2 * half_bps + comm_bps
            rows.append(dict(ticker=tk, date=d, open=o, p945=p945, p950=p950,
                             r45=r45, r50=r50, gap=gap, prev_ret=prevret,
                             half_spread_bps=half_bps, cost_bps=cost_bps,
                             short_gross_bps=-r45 * 1e4,
                             short_net_bps=-r45 * 1e4 - cost_bps,
                             short_gross_bps_50=(-r50 * 1e4 if np.isfinite(r50) else np.nan),
                             short_net_bps_50=(-r50 * 1e4 - cost_bps if np.isfinite(r50) else np.nan)))
        prior_prior_close = prior_close
        prior_close = day_close

sd = pd.DataFrame(rows)
sd.to_csv(os.path.join(BASE, 'per_stockday.csv'), index=False)

out = []
def P(s=''):
    out.append(s); print(s)

def tstat(x):
    x = pd.Series(x).dropna()
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 and x.std() > 0 else np.nan

P(f'stock-days: {len(sd)}  tickers: {sd.ticker.nunique()}  '
  f'sessions: {sd.date.nunique()}  {sd.date.min()} -> {sd.date.max()}')

# --- 1/2 descriptive ---
for col, lbl in [('r45', '09:30->09:45'), ('r50', '09:30->09:50')]:
    x = sd[col].dropna()
    P(f'\n[{lbl}] frac stock-days r<0: {(x<0).mean():.4f}  (r<=0: {(x<=0).mean():.4f})  '
      f'mean {x.mean()*1e4:+.2f} bps  median {x.median()*1e4:+.2f} bps  t={tstat(x):.2f}')
daily = sd.groupby('date').apply(lambda g: (g['r45'] < 0).mean(), include_groups=False)
P(f'\nper-day cross-sectional frac down @09:45: mean {daily.mean():.3f} median {daily.median():.3f} '
  f'min {daily.min():.3f} max {daily.max():.3f}; days with >50% down: {(daily>0.5).sum()}/{len(daily)}')
P('per-day frac-down distribution: ' + ' '.join(
    f'p{q}={daily.quantile(q/100):.3f}' for q in [10, 25, 50, 75, 90]))

# --- 3 short P&L ---
for suf, lbl in [('', 'cover 09:45'), ('_50', 'cover 09:50')]:
    g_, n_ = sd['short_gross_bps' + suf].dropna(), sd['short_net_bps' + suf].dropna()
    P(f'\n[SHORT @open, {lbl}] gross mean {g_.mean():+.2f} bps (t={tstat(g_):.2f}), '
      f'median {g_.median():+.2f}; NET mean {n_.mean():+.2f} bps (t={tstat(n_):.2f}), '
      f'median {n_.median():+.2f}; frac net>0: {(n_>0).mean():.4f}')
P(f'avg total cost: {sd.cost_bps.mean():.2f} bps roundtrip '
  f'(spread floors 3/5 bps/side + commission)')

# --- 4 conditionals ---
sd['gap_decile'] = pd.qcut(sd['gap'], 10, labels=False, duplicates='drop')
P('\n[GAP DECILES] (decile 9 = biggest gap-up); short@open cover 09:45, per cell:')
P(f'{"dec":>4} {"n":>5} {"gap_mid%":>9} {"gross_bps":>10} {"net_bps":>9} {"t(net)":>7} {"%net>0":>7}')
for dec, g in sd.groupby('gap_decile'):
    P(f'{dec:>4} {len(g):>5} {g.gap.median()*100:>9.2f} {g.short_gross_bps.mean():>10.2f} '
      f'{g.short_net_bps.mean():>9.2f} {tstat(g.short_net_bps):>7.2f} {(g.short_net_bps>0).mean():>7.3f}')

P('\n[PRIOR-DAY RETURN SIGN]')
for lbl2, g in [('prev day UP', sd[sd.prev_ret > 0]), ('prev day DOWN', sd[sd.prev_ret < 0])]:
    P(f'  {lbl2}: n={len(g)} gross {g.short_gross_bps.mean():+.2f} net {g.short_net_bps.mean():+.2f} '
      f'bps t(net)={tstat(g.short_net_bps):.2f} %net>0 {(g.short_net_bps>0).mean():.3f}')

top = sd[sd.gap_decile == sd.gap_decile.max()]
P(f'\n[TOP GAP-UP DECILE] n={len(top)} median gap {top.gap.median()*100:+.2f}%: '
  f'gross {top.short_gross_bps.mean():+.2f} net {top.short_net_bps.mean():+.2f} bps '
  f't(net)={tstat(top.short_net_bps):.2f} %net>0 {(top.short_net_bps>0).mean():.3f}')

# biggest gap-up name each day
bgu = sd.loc[sd.groupby('date')['gap'].idxmax()]
P(f'[BIGGEST GAP-UP NAME EACH DAY] n={len(bgu)} days: gross {bgu.short_gross_bps.mean():+.2f} '
  f'net {bgu.short_net_bps.mean():+.2f} bps t(net)={tstat(bgu.short_net_bps):.2f} '
  f'%days net>0 {(bgu.short_net_bps>0).mean():.3f}')

# --- 5 dollar math ---
medpx = sd.groupby('ticker')['open'].median().median()
notional = 10 * medpx
P(f'\n[DOLLAR MATH] median-priced name ~${medpx:.2f} -> 10 shares = ${notional:.0f} notional')
un_g = sd.short_gross_bps.mean() / 1e4 * notional
un_n = sd.short_net_bps.mean() / 1e4 * notional
P(f'  (a) unconditional random name: gross ${un_g:+.3f}/day, net ${un_n:+.3f}/day')
bg_g = bgu.short_gross_bps.mean() / 1e4 * notional
bg_n = bgu.short_net_bps.mean() / 1e4 * notional
P(f'  (b) biggest gap-up name daily: gross ${bg_g:+.3f}/day, net ${bg_n:+.3f}/day')
cells = {'unconditional': sd.short_net_bps.mean(),
         'top gap decile': top.short_net_bps.mean(),
         'biggest gap-up daily': bgu.short_net_bps.mean(),
         'prev-day-down': sd[sd.prev_ret < 0].short_net_bps.mean()}
best_name, best_bps = max(cells.items(), key=lambda kv: kv[1])
P(f'  best conditional cell: "{best_name}" at {best_bps:+.2f} net bps/day')
if best_bps > 0:
    P(f'  notional for $50/day IF real: ${50/(best_bps/1e4):,.0f}')
else:
    P('  no cell is net-positive -> NO notional achieves $50/day; scaling scales the loss.')

# --- regime ---
try:
    vix = pd.read_parquet('/Users/varmakammili/Documents/GitHub/StocksTradingTest/'
                          'bps_rederive_2026_07/data/vix_daily_cboe.parquet')
    vc = vix.iloc[:, -1] if not isinstance(vix, pd.Series) else vix
    vwin = vc.loc[str(sd.date.min()):str(sd.date.max())]
    P(f'\n[REGIME] Cboe VIX over window: mean {vwin.mean():.1f} '
      f'min {vwin.min():.1f} max {vwin.max():.1f} last {vwin.iloc[-1]:.1f} '
      f'(n={len(vwin)} days; data through {vc.index[-1]})' if len(vwin)
      else f'\n[REGIME] Cboe VIX cache ends {vc.index[-1]} (before window); last value {vc.iloc[-1]:.1f}')
except Exception as e:
    P(f'\n[REGIME] VIX cache read failed: {e}')

with open(os.path.join(BASE, 'study_output.txt'), 'w') as f:
    f.write('\n'.join(out) + '\n')
