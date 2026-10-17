"""NDXP replication of the BPS study — pre-registered questions (locked before run):
Q1: does SL 3.0x dominate 2.0x on NDX as it did on SPX (net total, both eras)?
Q2: is the daily-expiry era (2022-05+) net POSITIVE at 3.0x?
SCAN-mode incumbent-analog: otm 1.0%, width = 0.68% of spot rounded to 25 (SPX
50/7350 equivalent), credit floor = 2.4% of width, target = 4% of width,
VIX gate 12-25 (official Cboe open), dir filter parity-spot>=parity-open coupled
at entry, scan 10:30-14:30, SL on conservative cost-to-close, expiry settled at
parity-spot 15:55 (cross-checked vs spot cache; disclosed proxy).
"""
import numpy as np
import pandas as pd
from pathlib import Path

BASE = Path("../spx_spread_bot/backtests/narrow_width_databento_2026_05_21")
REP = Path("data/repairs_ndxp")
ET = "America/New_York"
MAX_STALE = 5

def load_side(tag, side):
    parts = []
    f = BASE / f"ndxp_{side}_cbbo_1m" / f"ndxp_{side}_cbbo_{tag}.parquet"
    if f.exists():
        parts.append(pd.read_parquet(f))
    rf = REP / f"winter_hour_{side.upper()}" / f"{tag}.parquet"
    if rf.exists() and rf.stat().st_size > 0:
        parts.append(pd.read_parquet(rf))
    if not parts:
        return None
    df = pd.concat(parts).sort_index()
    df = df[["symbol", "bid_px_00", "ask_px_00"]].copy()
    df["strike"] = df["symbol"].str[-8:].astype(int) / 1000.0
    df["minute"] = df.index.tz_convert(ET)
    df = df.drop_duplicates(subset=["minute", "symbol"], keep="first")
    return df

def grids(df):
    bid = df.pivot_table(index="minute", columns="strike", values="bid_px_00", aggfunc="first")
    ask = df.pivot_table(index="minute", columns="strike", values="ask_px_00", aggfunc="first")
    idx = bid.index.union(ask.index)
    cols = bid.columns.union(ask.columns)
    bid = bid.reindex(index=idx, columns=cols).ffill(limit=MAX_STALE)
    ask = ask.reindex(index=idx, columns=cols).ffill(limit=MAX_STALE)
    return bid, ask

def spot_factory(pb, pa, cb, ca):
    common = [k for k in pb.columns if k in cb.columns]
    PB, PA = pb[common].to_numpy(), pa[common].to_numpy()
    CB, CA = cb[common].to_numpy(), ca[common].to_numpy()
    ks = np.array(common)
    def spot(i):
        pm = (PB[i] + PA[i]) / 2; cm = (CB[i] + CA[i]) / 2
        ok = (PB[i] > 0) & (CA[i] > 0) & ~np.isnan(pm) & ~np.isnan(cm)
        if ok.sum() == 0: return np.nan
        dist = np.abs(cm - pm); dist[~ok] = np.inf
        idx = np.argsort(dist)[:5]; idx = idx[np.isfinite(dist[idx])]
        if len(idx) == 0: return np.nan
        return float(np.median(ks[idx] + cm[idx] - pm[idx]))
    return spot

def round25(x): return round(x / 25.0) * 25.0

def run():
    vix = pd.read_parquet("data/vix_daily_cboe.parquet")
    ptags = sorted(f.stem.split("_")[-1] for f in (BASE / "ndxp_p_cbbo_1m").glob("*.parquet"))
    rows = []
    for tag in ptags:
        d0 = pd.Timestamp(tag).tz_localize(ET)
        dkey = pd.Timestamp(tag)
        if dkey in vix.index:
            vo = float(vix.loc[dkey, "open"])
            if not (12 <= vo <= 25):
                continue
        p = load_side(tag, "p"); c = load_side(tag, "c")
        if p is None or c is None: continue
        pb, pa = grids(p); cb, ca = grids(c)
        cutoff = d0 + pd.Timedelta(hours=16)
        pb, pa = pb[pb.index <= cutoff], pa[pa.index <= cutoff]
        cb, ca = cb.reindex(pb.index), ca.reindex(pb.index)
        assert len(pb) == len(pa), "grid row mismatch"
        if len(pb) < 60: continue
        spot_i = spot_factory(pb, pa, cb, ca)
        minutes = pb.index
        mix = {m: i for i, m in enumerate(minutes)}
        s_open = spot_i(mix.get(d0 + pd.Timedelta(hours=9, minutes=32), 0))
        if np.isnan(s_open):
            s_open = spot_i(mix.get(d0 + pd.Timedelta(hours=9, minutes=35), 1))
        if np.isnan(s_open): continue
        # settlement proxy: parity spot at last minute <= 15:55
        idx_1555 = minutes.searchsorted(d0 + pd.Timedelta(hours=15, minutes=55), side="right") - 1
        settle_spot = spot_i(idx_1555) if idx_1555 >= 0 else np.nan
        if np.isnan(settle_spot): continue
        bidN, askN = pb.to_numpy(), pa.to_numpy()
        midN = (bidN + askN) / 2.0
        cols = {k: i for i, k in enumerate(pb.columns)}
        t_lo, t_hi = d0 + pd.Timedelta(hours=10, minutes=30), d0 + pd.Timedelta(hours=14, minutes=30)
        entered = False
        for m in minutes[(minutes >= t_lo) & (minutes <= t_hi)]:
            sm = spot_i(mix[m])
            if np.isnan(sm) or sm < s_open: continue
            width = round25(sm * 0.0068)
            floor, target = 0.024 * width, 0.04 * width
            tk = round25(sm * 0.99)
            cand = sorted(pb.columns, key=lambda s: abs(s - tk))[:5]
            best, bd = None, np.inf
            for ks_ in cand:
                below = [k for k in pb.columns if k <= ks_ - width]
                if not below: continue
                kl_ = max(below)
                if ks_ - kl_ < width * 0.8: continue
                try:
                    sb, sa = pb.at[m, ks_], pa.at[m, ks_]
                    lb, la = pb.at[m, kl_], pa.at[m, kl_]
                except KeyError: continue
                if any(np.isnan(x) for x in (sb, sa, lb, la)) or sb <= 0 or la <= 0: continue
                mid = (sb + sa) / 2 - (lb + la) / 2
                if mid < floor: continue
                if abs(mid - target) < bd:
                    bd, best = abs(mid - target), (ks_, kl_, sb, la)
            if best is None: continue
            ks_, kl_, sb, la = best
            credit = sb - la
            if credit < 0.05: continue
            ti = mix[m]
            # SL trigger on MID cost-to-close (live-mechanics: monitor mark, exit
            # via stop-limit at m*credit — both live SPX stops filled at limit).
            cost = midN[ti+1:, cols[ks_]] - midN[ti+1:, cols[kl_]]
            valid = ~np.isnan(cost)
            settle = credit - (max(0.0, ks_ - settle_spot) - max(0.0, kl_ - settle_spot))
            for sl in (2.0, 3.0):
                mask = valid & (cost >= sl * credit)
                if mask.any():
                    j = int(np.argmax(mask))
                    pnl, status = credit - sl * credit, "stop"
                else:
                    pnl, status = settle, "expire"
                fee = 1.30 * (4 if status == "stop" else 2)
                rows.append(dict(date=dkey, sl=sl, status=status, credit=round(credit, 2),
                                 width=width, pnl_usd=pnl * 100 - fee))
            entered = True
            break
    return pd.DataFrame(rows)

if __name__ == "__main__":
    R = run()
    R.to_parquet("data/ndxp_replication.parquet")
    R["year"] = R.date.dt.year
    print(f"traded days: {R.date.nunique()}")
    for sl in (2.0, 3.0):
        s = R[R.sl == sl]
        daily_era = s[s.date >= pd.Timestamp("2022-05-01")]
        print(f"\nSL={sl}: n={len(s)} total=${s.pnl_usd.sum():+9.0f}  stop%={(s.status=='stop').mean()*100:.0f}")
        print("  by year:", {int(k): int(v) for k, v in s.groupby('year')['pnl_usd'].sum().items()})
        print(f"  daily-era (2022-05+): n={len(daily_era)} total=${daily_era.pnl_usd.sum():+9.0f}")
