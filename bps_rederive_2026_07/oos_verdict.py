"""THE single OOS evaluation. Reads ONLY the four locked incumbent rows
(SURVIVORS.md). Reports full OOS 2021-2026 + the 2022-04-18+ daily-era subset,
HAC t-stats, annualized Sharpe, BH-FDR at 10% across the 4 looks.
"""
import numpy as np, pandas as pd
from scipy import stats as sps

def nw(x, lags=5):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    n = len(x)
    if n < 10: return np.nan, n
    mu = x.mean(); e = x - mu; var = (e @ e) / n
    for k in range(1, lags + 1):
        var += 2 * (1 - k / (lags + 1)) * ((e[k:] @ e[:-k]) / n)
    se = np.sqrt(var / n)
    return (mu / se if se > 0 else np.nan), n

def sharpe_ann(x, trades_per_year):
    x = np.asarray(x, float)
    if len(x) < 10 or x.std() == 0: return np.nan
    return x.mean() / x.std() * np.sqrt(trades_per_year)

def main():
    parts = [pd.read_parquet(f"data/sweep/sweep_{y}.parquet") for y in range(2021, 2027)]
    df = pd.concat(parts, ignore_index=True)
    inc = df[df.entry == "SCAN1030"].copy()
    inc = inc[(inc.vix_open >= 12) & (inc.vix_open <= 25)]
    inc["year"] = inc.date.dt.year
    daily_era = inc[inc.date >= pd.Timestamp("2022-04-18")]

    pvals = []
    print("=" * 100)
    print("OOS SINGLE SHOT — incumbent (SCAN1030 w50 otm1.0 dir+vix gates) 2021-01-01 -> 2026-07-02")
    print("=" * 100)
    for label, sub in (("FULL OOS 2021-2026", inc), ("DAILY-ERA 2022-04-18+", daily_era)):
        print(f"\n--- {label} ---")
        for sl in (0.0, 1.5, 2.0, 3.0):
            s = sub[sub.sl == sl]
            x = s.pnl_usd
            t, n = nw(x.values)
            yrs = (s.date.max() - s.date.min()).days / 365.25 if len(s) > 1 else np.nan
            tpy = n / yrs if yrs and yrs > 0 else np.nan
            sh = sharpe_ann(x.values, tpy) if tpy else np.nan
            yr_tot = s.groupby("year")["pnl_usd"].sum().round(0).astype(int).to_dict()
            tag = " <- LIVE" if sl == 2.0 else (" <- planned" if sl == 3.0 else "")
            print(f"SL={sl:<4} n={n:<4} total=${x.sum():+9.0f}  mean=${x.mean():+7.2f}  t={t:+5.2f}  "
                  f"Sharpe={sh:+5.2f}  %pos={(x>0).mean()*100:3.0f}  worst=${x.min():+7.0f}  "
                  f"stop%={(s.status=='stop').mean()*100:3.0f}{tag}")
            print(f"        by year: {yr_tot}")
            if label.startswith("FULL"):
                # one-sided p for mean>0 from HAC t (normal approx)
                p = 1 - sps.norm.cdf(t) if not np.isnan(t) else np.nan
                pvals.append((sl, p))
    # BH-FDR 10% across the 4 pre-registered looks
    print("\n--- Benjamini-Hochberg FDR 10% across the 4 locked looks (full OOS) ---")
    valid = [(sl, p) for sl, p in pvals if not np.isnan(p)]
    m = len(valid)
    for rank, (sl, p) in enumerate(sorted(valid, key=lambda z: z[1]), 1):
        thresh = 0.10 * rank / m
        print(f"SL={sl}: p={p:.3f}  BH-threshold={thresh:.3f}  {'PASS' if p <= thresh else 'fail'}")

if __name__ == "__main__":
    main()
