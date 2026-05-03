# fetch_history.py — Pull 2 years of MES 1-minute OHLCV data and save to CSV.
#
# Sources (try in order):
#   1. Databento  — cleanest, free tier, handles rollovers automatically
#   2. IBKR       — direct pull if TWS is running and HMDS works
#   3. yfinance   — 30-day fallback only (logs a warning)
#
# Usage:
#   python fetch_history.py                        # Databento (needs API key)
#   python fetch_history.py --source ibkr          # IBKR pull
#   python fetch_history.py --source yfinance      # quick 30-day test
#   python fetch_history.py --key YOUR_KEY         # pass Databento key inline
#
# Output: data/mes_1min.csv  (auto-created, reused by backtest.py)
#
# Databento free tier: sign up at https://databento.com → API Keys → copy key
# Dataset: GLBX.MDP3 (CME Globex)  Symbol: MES.c.0 (front-month continuous)

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

TZ       = ZoneInfo("America/New_York")
DATA_DIR = Path(__file__).parent / "data"
OUT_CSV  = DATA_DIR / "mes_1min.csv"
DATA_DIR.mkdir(exist_ok=True)


# ── Databento ──────────────────────────────────────────────────────────────────

def fetch_databento(api_key: str, start: str, end: str, auto_yes: bool = False) -> pd.DataFrame:
    """
    Pull MES front-month continuous 1-min OHLCV from Databento.
    MES.c.0 = front-month continuous, auto-adjusted for rolls.
    """
    try:
        import databento as db
    except ImportError:
        print("Run: pip install databento")
        sys.exit(1)

    print(f"Databento: fetching MES.c.0 1-min  {start} → {end}")
    print("(Estimating cost before download...)\n")

    client = db.Historical(key=api_key)

    # Cost estimate first — avoid surprise charges
    cost = client.metadata.get_cost(
        dataset="GLBX.MDP3",
        symbols=["MES.c.0"],
        schema="ohlcv-1m",
        stype_in="continuous",
        start=start,
        end=end,
    )
    print(f"  Estimated cost: ${cost:.4f}  (free-tier credit: $125)")
    if not auto_yes:
        confirm = input("  Proceed? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            sys.exit(0)

    data = client.timeseries.get_range(
        dataset="GLBX.MDP3",
        symbols=["MES.c.0"],
        schema="ohlcv-1m",
        stype_in="continuous",
        start=start,
        end=end,
    )

    df = data.to_df()
    print(f"  Raw rows: {len(df):,}")

    # Normalise columns
    df = df.rename(columns={
        "open":   "open",
        "high":   "high",
        "low":    "low",
        "close":  "close",
        "volume": "volume",
    })
    df = df[["open", "high", "low", "close", "volume"]].copy()

    # Convert index to ET
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(TZ)

    # Scale: Databento stores prices in fixed-point (divide by 1e9 for actual price)
    for col in ["open", "high", "low", "close"]:
        if df[col].max() > 1_000_000:
            df[col] = df[col] / 1e9

    return df


# ── IBKR ──────────────────────────────────────────────────────────────────────

def fetch_ibkr(start_date: str, end_date: str) -> pd.DataFrame:
    """
    Pull MES 1-min bars from IBKR in 7-day chunks.
    Requires TWS running on port 7497 with HMDS accessible.
    Uses clientId=2 to avoid conflicting with the live bot (clientId=1).
    """
    try:
        from ib_insync import IB, Future
    except ImportError:
        print("Run: pip install ib_insync")
        sys.exit(1)

    ib = IB()
    print("Connecting to TWS (clientId=2)...")
    try:
        ib.connect("127.0.0.1", 7497, clientId=2, timeout=20)
        ib.reqMarketDataType(1)   # live data type needed for HMDS on some accounts
    except Exception as e:
        print(f"Connection failed: {e}")
        print("Make sure TWS is running and the API is enabled.")
        sys.exit(1)

    # Resolve front-month MES
    probe = Future(symbol="MES", exchange="CME", currency="USD")
    details = ib.reqContractDetails(probe)
    now = datetime.now(TZ)
    candidates = []
    for d in details:
        c = d.contract
        exp = c.lastTradeDateOrContractMonth
        try:
            exp_dt = datetime.strptime(exp[:8], "%Y%m%d").replace(tzinfo=TZ)
            if exp_dt > now + timedelta(days=5):
                candidates.append((exp_dt, c))
        except ValueError:
            continue
    if not candidates:
        print("Could not resolve MES contract.")
        ib.disconnect()
        sys.exit(1)
    candidates.sort()
    contract = ib.qualifyContracts(candidates[0][1])[0]
    print(f"Contract: {contract.localSymbol}  expiry={contract.lastTradeDateOrContractMonth}")

    # Pull in 7-day chunks
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=TZ)
    end_dt   = datetime.strptime(end_date,   "%Y-%m-%d").replace(tzinfo=TZ)

    all_bars = []
    cursor   = end_dt
    while cursor > start_dt:
        chunk_start = max(cursor - timedelta(days=7), start_dt)
        end_str     = cursor.strftime("%Y%m%d %H:%M:%S")
        days        = (cursor - chunk_start).days + 1
        print(f"  Pulling {days}D ending {end_str}...", end=" ", flush=True)
        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=end_str,
                durationStr=f"{days} D",
                barSizeSetting="1 min",
                whatToShow="TRADES",
                useRTH=False,
                formatDate=2,
            )
            print(f"{len(bars)} bars")
            all_bars.extend(bars)
        except Exception as e:
            print(f"ERROR: {e}")
        cursor = chunk_start
        time.sleep(0.5)

    ib.disconnect()

    if not all_bars:
        print("No bars pulled from IBKR.")
        sys.exit(1)

    rows = []
    for b in all_bars:
        dt = b.date if isinstance(b.date, datetime) else \
             datetime.fromtimestamp(float(b.date), tz=TZ)
        rows.append({
            "datetime": dt.astimezone(TZ),
            "open":  b.open,
            "high":  b.high,
            "low":   b.low,
            "close": b.close,
            "volume": b.volume,
        })

    df = pd.DataFrame(rows).set_index("datetime").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


# ── yfinance (30-day fallback) ─────────────────────────────────────────────────

def fetch_yfinance_chunks(days_back: int = 29) -> pd.DataFrame:
    import yfinance as yf
    import warnings
    warnings.filterwarnings("ignore")

    print(f"yfinance: fetching ES=F 1-min ({days_back}d in 7-day chunks)...")
    print("WARNING: yfinance caps at ~30 days. Use Databento for 2-year history.\n")

    end    = datetime.now(timezone.utc)
    pieces = []
    cursor = end
    remaining = days_back

    while remaining > 0:
        pull  = min(7, remaining)
        start = cursor - timedelta(days=pull)
        raw   = yf.download(
            "ES=F",
            start=start.strftime("%Y-%m-%d"),
            end=cursor.strftime("%Y-%m-%d"),
            interval="1m",
            progress=False,
        )
        if not raw.empty:
            pieces.append(raw)
        cursor    = start
        remaining -= pull
        time.sleep(0.4)

    if not pieces:
        print("yfinance returned no data.")
        sys.exit(1)

    combined = pd.concat(pieces[::-1]).sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]
    combined.columns = [c[0].lower() for c in combined.columns]
    combined.index   = combined.index.tz_convert(TZ)
    return combined


# ── Save / load ────────────────────────────────────────────────────────────────

def save_csv(df: pd.DataFrame) -> None:
    df.to_csv(OUT_CSV)
    print(f"\nSaved {len(df):,} rows → {OUT_CSV}")
    rth   = df.between_time("09:30", "16:00")
    days  = len(set(rth.index.date))
    print(f"RTH bars: {len(rth):,}  |  Trading days: {days}")


def load_csv() -> pd.DataFrame:
    df = pd.read_csv(OUT_CSV, index_col=0, parse_dates=True)
    if df.index.tz is None:
        df.index = pd.DatetimeIndex(df.index).tz_localize(TZ)
    else:
        df.index = df.index.tz_convert(TZ)
    return df


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch MES historical 1-min data")
    parser.add_argument("--source", choices=["databento", "ibkr", "yfinance"],
                        default="databento")
    parser.add_argument("--key",   type=str, default=os.environ.get("DATABENTO_API_KEY", ""),
                        help="Databento API key (or set DATABENTO_API_KEY env var)")
    parser.add_argument("--yes",   action="store_true",
                        help="Skip cost confirmation prompt")
    parser.add_argument("--start", type=str, default="2024-01-01",
                        help="Start date YYYY-MM-DD (default 2024-01-01)")
    parser.add_argument("--end",   type=str,
                        default=date.today().isoformat(),
                        help="End date YYYY-MM-DD (default today)")
    parser.add_argument("--days",  type=int, default=29,
                        help="Days back for yfinance source (default 29)")
    args = parser.parse_args()

    if args.source == "databento":
        if not args.key:
            print("""
Databento API key required. Steps:
  1. Sign up free at https://databento.com
  2. Go to API Keys in your dashboard
  3. Copy your key, then run:

     python fetch_history.py --key db-XXXXXXXXXXXXXXXXXXXX

  Or set it once as an env var:
     export DATABENTO_API_KEY=db-XXXXXXXXXXXXXXXXXXXX
     python fetch_history.py
""")
            sys.exit(1)
        df = fetch_databento(args.key, args.start, args.end, auto_yes=args.yes)

    elif args.source == "ibkr":
        df = fetch_ibkr(args.start, args.end)

    else:
        df = fetch_yfinance_chunks(args.days)

    save_csv(df)
    print("\nDone. Run backtest with this data:")
    print("  python backtest.py")


if __name__ == "__main__":
    main()
