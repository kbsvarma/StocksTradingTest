"""Comprehensive 0DTE option chain puller — covers all products, both sides.

Wider strike band (0-8% OTM) so no analyzer hits "long leg outside band" again.

Usage:
    python -m spx_spread_bot.backtests.narrow_width_databento_2026_05_21.pull_all \\
        --root SPXW --side P --start 2013-04-01 --end 2026-05-22 \\
        --kill-at-et 08:30 --confirm

Each (root, side) is its own process so parallel kicks are easy.

Auto-kill: if --kill-at-et HH:MM is passed, the script exits cleanly when
local clock crosses that ET time. Protects the trading session.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

# Global signal flag — set by SIGTERM/SIGINT, polled between days
_KILL_REQUESTED = False

def _on_kill_signal(signum, frame):
    global _KILL_REQUESTED
    _KILL_REQUESTED = True
    print(f"\n[signal] received signal {signum}, will exit cleanly after current day completes",
          flush=True)

signal.signal(signal.SIGTERM, _on_kill_signal)
signal.signal(signal.SIGINT, _on_kill_signal)

import databento as db
import pandas as pd
import yfinance as yf

ET = ZoneInfo("America/New_York")

ROOT_SPEC = {
    # root → (yf_spot_proxy, spot_scale, strike_spacing, expiry_pattern, out_subdir_prefix)
    "SPXW":  ("^GSPC", 1.0,   5.0,   "0DTE",      "spxw"),
    "SPX":   ("^GSPC", 1.0,   5.0,   "weekly",    "spx_weekly"),
    "SPXP":  ("^GSPC", 1.0,   5.0,   "0DTE",      "spxp"),
    "XSP":   ("^GSPC", 0.1,   1.0,   "0DTE",      "xsp"),    # 0.1× SPX scale, $1 strikes
    "SPY":   ("SPY",   1.0,   1.0,   "0DTE",      "spy"),
    "NDXP":  ("^NDX",  1.0,   25.0,  "0DTE",      "ndxp"),
    "NDX":   ("^NDX",  1.0,   25.0,  "weekly",    "ndx_weekly"),
    "RUT":   ("^RUT",  1.0,   5.0,   "weekly",    "rut"),
    "IWM":   ("IWM",   1.0,   1.0,   "0DTE",      "iwm"),
}


def _spot_history(yf_sym: str, start: str, end: str, cache: Path) -> pd.DataFrame:
    if cache.exists():
        return pd.read_parquet(cache)
    print(f"[pull] fetching {yf_sym} daily ({start}→{end})...")
    df = yf.Ticker(yf_sym).history(start=start, end=end)[["Open", "High", "Low", "Close"]]
    df.index = df.index.tz_localize(None) if df.index.tz else df.index
    df.to_parquet(cache)
    return df


def _build_strikes(spot: float, spacing: float, scale_to_spx: float = 1.0,
                   band_min_pct: float = 0.0, band_max_pct: float = 8.0,
                   width_buffer: float = 0.0) -> list[float]:
    """Strike list covering band_min_pct → band_max_pct OTM at native spacing."""
    lo = spot * (1 - band_max_pct / 100) - width_buffer
    hi = spot * (1 - band_min_pct / 100) + width_buffer
    lo_snap = int(lo // spacing) * spacing
    hi_snap = int(hi // spacing + 1) * spacing
    n = int((hi_snap - lo_snap) / spacing) + 1
    return [lo_snap + i * spacing for i in range(n)]


def _format_symbol(root: str, expiry: datetime, side: str, strike: float) -> str:
    """OPRA OCC: ROOT(6) + YYMMDD + C/P + STRIKE(8d = 5$.3¢)."""
    root_pad = root.ljust(6)
    yy = expiry.strftime("%y%m%d")
    return f"{root_pad}{yy}{side}{int(round(strike*1000)):08d}"


def _pull_one_day(client, day, spot, root, spacing, scale, side, out_dir):
    outfile = out_dir / f"{root.lower()}_{side.lower()}_cbbo_{day.strftime('%Y%m%d')}.parquet"
    if outfile.exists():
        return True, 0.0, "skip"
    scaled_spot = spot * scale
    strikes = _build_strikes(scaled_spot, spacing, scale, 0.0, 8.0)
    symbols = [_format_symbol(root, day, side, k) for k in strikes]
    start_iso = day.strftime("%Y-%m-%dT13:30:00")
    end_iso = day.strftime("%Y-%m-%dT20:01:00")
    try:
        cost = client.metadata.get_cost(
            dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=symbols,
            stype_in="raw_symbol", start=start_iso, end=end_iso,
        )
    except Exception as exc:
        return False, 0.0, f"cost-fail: {str(exc)[:80]}"
    if cost > 2.0:
        return False, cost, f"cost ${cost:.3f} > $2 ceiling"
    try:
        data = client.timeseries.get_range(
            dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=symbols,
            stype_in="raw_symbol", start=start_iso, end=end_iso,
        )
        df = data.to_df()
        if df.empty:
            return True, cost, "empty"
        # ATOMIC WRITE: tmp → fsync → rename. Prevents partial-file corruption
        # if process is killed mid-write. Resume will see only complete files.
        tmp = outfile.with_suffix(outfile.suffix + ".tmp")
        df.to_parquet(tmp)
        # fsync via os to ensure data is on disk before rename
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        tmp.rename(outfile)
        return True, cost, f"{len(df)}r"
    except Exception as exc:
        # Clean up any partial tmp file on error
        tmp = outfile.with_suffix(outfile.suffix + ".tmp")
        if tmp.exists():
            try: tmp.unlink()
            except: pass
        return False, cost, f"pull-fail: {str(exc)[:80]}"


def _check_kill_time(kill_at: dtime | None) -> bool:
    if not kill_at: return False
    now = datetime.now(ET).time()
    return now >= kill_at


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, choices=list(ROOT_SPEC))
    ap.add_argument("--side", required=True, choices=["P", "C"])
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--kill-at-et", default=None, help="HH:MM ET — exit cleanly at this time")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args()

    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("ERROR: DATABENTO_API_KEY env required"); sys.exit(1)
    client = db.Historical(key)

    yf_sym, spot_scale, spacing, expiry_pat, prefix = ROOT_SPEC[args.root]
    base = Path(__file__).parent
    out_dir = base / f"{prefix}_{args.side.lower()}_cbbo_1m"
    out_dir.mkdir(exist_ok=True)
    # RESUME SAFETY: clean any stale .tmp files left by a prior killed run
    for stale in out_dir.glob("*.tmp"):
        try:
            stale.unlink()
            print(f"[resume] cleaned stale tmp: {stale.name}", flush=True)
        except Exception:
            pass
    # ALSO: delete any zero/tiny parquet files (likely partial from prior unsafe kill)
    # Real parquet with even 1 row has ~2KB metadata. Threshold 1KB is safe.
    cleaned = 0
    for fp in out_dir.glob("*.parquet"):
        try:
            if fp.stat().st_size < 1024:
                fp.unlink()
                cleaned += 1
        except Exception:
            pass
    if cleaned:
        print(f"[resume] cleaned {cleaned} partial parquet files (<1KB)", flush=True)
    cache = base / f"spot_cache_{yf_sym.replace('^','')}.parquet"

    spot_df = _spot_history(yf_sym, args.start, args.end, cache)
    days = [pd.Timestamp(d) for d in spot_df.index
            if pd.Timestamp(args.start) <= pd.Timestamp(d) <= pd.Timestamp(args.end)]

    kill_at = None
    if args.kill_at_et:
        h, m = args.kill_at_et.split(":")
        kill_at = dtime(int(h), int(m))

    label = f"{args.root}/{args.side}"
    print(f"[{label}] target {len(days)} days, band 0-8% OTM, kill@ET={args.kill_at_et}")
    if not args.confirm:
        print("Pass --confirm to start"); sys.exit(0)

    total_cost = 0.0; pulled = 0; empty = 0; failed = 0; skipped = 0
    t0 = time.monotonic()
    for i, day in enumerate(days):
        if _check_kill_time(kill_at):
            print(f"[{label}] kill-time reached, exiting cleanly at day {i+1}/{len(days)}")
            break
        if _KILL_REQUESTED:
            print(f"[{label}] signal-kill received, exiting cleanly at day {i+1}/{len(days)}")
            break
        try:
            spot = float(spot_df.loc[day, "Open"])
        except Exception:
            failed += 1; continue
        ok, cost, msg = _pull_one_day(client, day, spot, args.root, spacing,
                                       spot_scale, args.side, out_dir)
        total_cost += cost
        if msg == "skip":
            skipped += 1
        elif not ok:
            failed += 1
            if failed <= 5 or failed % 20 == 0:
                print(f"[{label}] [{i+1}/{len(days)}] {day.date()} FAIL: {msg}")
        elif msg == "empty":
            empty += 1
        else:
            pulled += 1
        if (i+1) % 250 == 0:
            print(f"[{label}] [{i+1}/{len(days)}] pulled={pulled} empty={empty} "
                  f"skipped={skipped} failed={failed} cost=${total_cost:.2f}")

    dt = time.monotonic() - t0
    print(f"[{label}] DONE in {dt/60:.1f}min: pulled={pulled} empty={empty} "
          f"skipped={skipped} failed={failed} cost=${total_cost:.3f}")


if __name__ == "__main__":
    main()
