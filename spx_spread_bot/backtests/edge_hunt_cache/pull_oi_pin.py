"""Pull open interest (statistics schema, stat_type=9) for SPX 0DTE Fridays
and AVGO M/W/F 0DTE days in 2026.

For each "trading day before expiry" (e.g. Thursday for Friday expiry),
query OI for the next-expiry option symbols at strikes near spot.
"""
import os, sys, time
from datetime import datetime, timedelta, date
from pathlib import Path
import databento as db
import pandas as pd

OUT = Path(__file__).parent / "pin_oi_data"
OUT.mkdir(exist_ok=True)
SPX_CACHE = Path(__file__).parent.parent / "narrow_width_databento_2026_05_21" / "spot_cache_GSPC.parquet"
SPX_PUT_DIR = Path(__file__).parent.parent / "narrow_width_databento_2026_05_21" / "spxw_p_cbbo_1m"
AVGO_DIR = Path(__file__).parent.parent / "avgo_ic_2025" / "data"

def prev_trading_day(d, spx_idx):
    """Find the trading day immediately before d (using SPX index)."""
    ts = pd.Timestamp(d)
    pos = spx_idx.get_indexer([ts], method="bfill")[0]
    if pos < 1: return None
    return spx_idx[pos-1].date()

def pull_oi_for(client, symbols, query_date, dry=False):
    s = f"{query_date.isoformat()}T00:00:00"
    e = f"{(query_date + timedelta(days=1)).isoformat()}T00:00:00"
    try:
        cost = client.metadata.get_cost(
            dataset="OPRA.PILLAR", schema="statistics", symbols=symbols,
            stype_in="raw_symbol", start=s, end=e,
        )
    except Exception as ex:
        return None, 0.0, f"cost-err: {ex}"
    if dry: return None, cost, "dry"
    try:
        data = client.timeseries.get_range(
            dataset="OPRA.PILLAR", schema="statistics", symbols=symbols,
            stype_in="raw_symbol", start=s, end=e,
        )
        df = data.to_df()
        if df.empty:
            return None, cost, "empty"
        # Filter stat_type 9 = open interest
        oi = df[df["stat_type"]==9].copy()
        if oi.empty:
            return None, cost, "no OI rows"
        # Per symbol take last OI of the day
        oi = oi.sort_values("ts_event").groupby("symbol").tail(1)
        return oi[["symbol","ts_event","quantity"]].reset_index(drop=True), cost, "ok"
    except Exception as ex:
        return None, cost, f"pull-err: {ex}"

def pull_spx():
    """For each Friday with 0DTE put data, pull OI on Thursday for Friday-expiry symbols."""
    spx = pd.read_parquet(SPX_CACHE)
    spx.index = pd.to_datetime(spx.index)
    # Find Fridays with 0DTE data in 2024-2025
    files = sorted(SPX_PUT_DIR.glob("spxw_p_cbbo_*.parquet"))
    fridays = []
    for f in files:
        ds = f.stem.split("_")[-1]
        try: d = datetime.strptime(ds, "%Y%m%d").date()
        except: continue
        if d.year not in (2024, 2025): continue
        if d.weekday() != 4: continue  # Friday
        fridays.append((d, f))
    print(f"[SPX] {len(fridays)} Friday 0DTE days in 2024-2025", flush=True)
    return fridays, spx

def pull_avgo():
    """AVGO M/W/F 0DTE days in Mar-May 2026."""
    spx = pd.read_parquet(SPX_CACHE)
    spx.index = pd.to_datetime(spx.index)
    files = sorted(AVGO_DIR.glob("avgo_cbbo_2026*.parquet"))
    days = []
    for f in files:
        ds = f.stem.split("_")[-1]
        try: d = datetime.strptime(ds, "%Y%m%d").date()
        except: continue
        if d.weekday() not in (0,2,4): continue  # M/W/F
        days.append((d, f))
    print(f"[AVGO] {len(days)} M/W/F 0DTE days in 2026", flush=True)
    return days, spx

def main():
    dry = "--dry" in sys.argv
    client = db.Historical(os.environ["DATABENTO_API_KEY"])

    spx_days, spx_idx = pull_spx()
    avgo_days, _ = pull_avgo()

    total_cost = 0.0
    t0 = time.monotonic()

    for label, items, root, otm_pct, grid in [
        ("SPX", spx_days, "SPXW", 0.03, 5.0),
        ("AVGO", avgo_days, "AVGO", 0.05, 2.5),
    ]:
        for i, (expiry_date, cbbo_fp) in enumerate(items):
            out = OUT / f"{label.lower()}_oi_{expiry_date.strftime('%Y%m%d')}.parquet"
            if out.exists() and not dry:
                continue

            # Need spot on prev trading day
            prev = prev_trading_day(expiry_date, spx_idx.index)
            if prev is None: continue
            try:
                spot = float(spx_idx.loc[pd.Timestamp(prev), "Close"])
            except KeyError:
                continue

            if label == "AVGO":
                # Use AVGO spot via separate cache OR approximation. Use close from prev day's file.
                # Read prev day's AVGO cbbo to get ATM-ish spot
                prev_avgo_fp = AVGO_DIR / f"avgo_cbbo_{prev.strftime('%Y%m%d')}.parquet"
                if not prev_avgo_fp.exists():
                    # fall back to expiry day's file
                    prev_avgo_fp = cbbo_fp
                try:
                    avgo_df = pd.read_parquet(prev_avgo_fp)
                    # Use ATM straddle approx: find strike where put+call sum is min (ATM)
                    # Simpler: use median strike of all symbols
                    import re
                    SRX = re.compile(r"[PC](\d{8})$")
                    strikes = []
                    for s in avgo_df["symbol"].unique():
                        m = SRX.search(s)
                        if m: strikes.append(int(m.group(1))/1000)
                    spot = sum(strikes)/len(strikes) if strikes else 300.0
                except Exception:
                    spot = 300.0

            # Build symbol list for this expiry
            lo = spot * (1 - otm_pct)
            hi = spot * (1 + otm_pct)
            lo_k = int(lo / grid) * grid
            hi_k = int(hi / grid + 1) * grid
            n = int((hi_k - lo_k) / grid) + 1
            strikes = [round(lo_k + j*grid, 2) for j in range(n)]
            yymmdd = expiry_date.strftime("%y%m%d")
            symbols = []
            for k in strikes:
                ks = int(round(k*1000))
                symbols.append(f"{root.ljust(6)}{yymmdd}P{ks:08d}")
                symbols.append(f"{root.ljust(6)}{yymmdd}C{ks:08d}")

            oi, cost, status = pull_oi_for(client, symbols, prev, dry=dry)
            total_cost += cost
            if not dry and oi is not None:
                oi.to_parquet(out)
            if i % 10 == 0:
                rate = (i+1)/(time.monotonic()-t0+0.01)
                print(f"  [{label} {i+1}/{len(items)}] expiry={expiry_date} prev={prev} spot={spot:.0f} "
                      f"strikes={len(strikes)} cost=${total_cost:.4f} ({status})", flush=True)

    dt = time.monotonic() - t0
    print(f"\n[done] {dt:.0f}s total_cost=${total_cost:.4f}", flush=True)

if __name__ == "__main__":
    main()
