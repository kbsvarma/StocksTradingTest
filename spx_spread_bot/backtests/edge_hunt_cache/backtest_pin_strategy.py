"""Pin-risk Iron Fly backtest.

Thesis: sell a tight IF centered at the high-OI strike on expiry day.
Pinning force drives price toward strike → both sides decay → IF profits.

Three OI definitions tested:
  (a) Single strike with MAX (call_OI + put_OI) — combined gravity
  (b) Strikes with max call-OI (sell call side here) and max put-OI separately
  (c) Gamma-flip strike where dealer net-gamma changes sign (approximated)

Also tests a CONTROL: same IF but centered at ATM (no OI info) — to isolate the pin edge.

For SPX: cash-settle at close (no exit slippage).
For AVGO: close trade at 15:55 ET using cbbo data.
"""
import re
from datetime import datetime, timedelta, date
from pathlib import Path
import pandas as pd
import numpy as np

CACHE = Path(__file__).parent
OI_DIR = CACHE / "pin_oi_data"
SPX_PUT_DIR = CACHE.parent / "narrow_width_databento_2026_05_21" / "spxw_p_cbbo_1m"
AVGO_DIR = CACHE.parent / "avgo_ic_2025" / "data"
SPX = pd.read_parquet(CACHE.parent / "narrow_width_databento_2026_05_21" / "spot_cache_GSPC.parquet")

RX_STRIKE = re.compile(r"([PC])(\d{8})$")
RX_EXP = re.compile(r"^([A-Z]+)\s+(\d{6})[PC]")

def parse_sym(s):
    """Return (strike_float, side P|C, expiry_date) or None."""
    s = s.strip()
    m1 = RX_STRIKE.search(s); m2 = RX_EXP.match(s)
    if not m1 or not m2: return None
    side = m1.group(1)
    strike = int(m1.group(2)) / 1000
    yymmdd = m2.group(2)
    exp = date(2000+int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6]))
    return strike, side, exp

def load_oi(label, expiry_date):
    fp = OI_DIR / f"{label.lower()}_oi_{expiry_date.strftime('%Y%m%d')}.parquet"
    if not fp.exists(): return None
    df = pd.read_parquet(fp)
    parsed = df["symbol"].apply(parse_sym)
    valid = parsed.notna()
    df = df[valid].copy()
    df["strike"] = parsed[valid].apply(lambda x: x[0])
    df["side"] = parsed[valid].apply(lambda x: x[1])
    df["expiration"] = parsed[valid].apply(lambda x: x[2])
    df = df[df["expiration"]==expiry_date].copy()
    df["oi"] = df["quantity"].astype(float)
    return df[["strike","side","oi"]].drop_duplicates(["strike","side"])

def pin_strike_combined(oi_df, spot, grid):
    """Definition (a): single strike with max combined OI."""
    if oi_df is None or oi_df.empty: return None
    agg = oi_df.groupby("strike")["oi"].sum().reset_index()
    # Restrict to strikes within ±3% of spot
    agg = agg[(agg["strike"] >= spot*0.97) & (agg["strike"] <= spot*1.03)]
    if agg.empty: return None
    return float(agg.loc[agg["oi"].idxmax(), "strike"])

def pin_strikes_separate(oi_df, spot):
    """Definition (b): max call_OI strike (for call side) and max put_OI strike."""
    if oi_df is None or oi_df.empty: return None, None
    call_oi = oi_df[oi_df["side"]=="C"]
    put_oi  = oi_df[oi_df["side"]=="P"]
    call_oi = call_oi[(call_oi["strike"] >= spot*0.97) & (call_oi["strike"] <= spot*1.03)]
    put_oi  = put_oi[(put_oi["strike"] >= spot*0.97) & (put_oi["strike"] <= spot*1.03)]
    c = float(call_oi.loc[call_oi["oi"].idxmax(), "strike"]) if not call_oi.empty else None
    p = float(put_oi.loc[put_oi["oi"].idxmax(), "strike"]) if not put_oi.empty else None
    return c, p

def pin_strike_gamma_flip(oi_df, spot):
    """Definition (c): approximate gamma-flip strike.

    Net-gamma proxy: cum_put_OI(K) below K, cum_call_OI(K) above K.
    Dealer net-gamma flips where cum_call ≈ cum_put. Use strike closest to that.
    Simplification: pick strike where |cum_call_above - cum_put_below| is min.
    """
    if oi_df is None or oi_df.empty: return None
    g = oi_df.groupby(["strike","side"])["oi"].sum().unstack(fill_value=0).reset_index()
    if "C" not in g or "P" not in g: return None
    g = g.sort_values("strike").reset_index(drop=True)
    g["cum_put"] = g["P"].cumsum()
    g["cum_call_above"] = g["C"][::-1].cumsum()[::-1]
    g["flip_score"] = (g["cum_call_above"] - g["cum_put"]).abs()
    g = g[(g["strike"] >= spot*0.97) & (g["strike"] <= spot*1.03)]
    if g.empty: return None
    return float(g.loc[g["flip_score"].idxmin(), "strike"])

# ===== SPX-side =====
def load_spx_cbbo_minute(expiry_date, hh_utc, mm_utc):
    fp = SPX_PUT_DIR / f"spxw_p_cbbo_{expiry_date.strftime('%Y%m%d')}.parquet"
    if not fp.exists(): return None
    df = pd.read_parquet(fp)
    df = df[(df["bid_px_00"]>0)&(df["ask_px_00"]>0)].copy()
    if df.empty: return None
    target = f"{hh_utc:02d}:{mm_utc:02d}"
    snap = df[df.index.strftime("%H:%M") == target].copy()
    if snap.empty: return None
    snap["strike"] = snap["symbol"].apply(lambda s: int(re.search(r"P0?(\d+)000$", s.strip()).group(1)))
    snap["put_mid"] = (snap["bid_px_00"]+snap["ask_px_00"])/2
    return snap[["strike","bid_px_00","ask_px_00","put_mid"]].drop_duplicates("strike").set_index("strike")

def spx_call_via_parity(put_mid, spot, strike):
    """C = P + S - K (cash-settled, ignore rates/divs for intraday SPX)."""
    return put_mid + spot - strike

def spx_pin_trade(expiry_date, center_strike, wing, hh, mm):
    """Sell IF centered at center_strike with given wing width.
       Settle at SPX close (cash-settled, no exit slippage)."""
    ts = pd.Timestamp(expiry_date)
    if ts not in SPX.index: return None
    spx_open = float(SPX.loc[ts, "Open"])
    spx_close = float(SPX.loc[ts, "Close"])
    puts = load_spx_cbbo_minute(expiry_date, hh, mm)
    if puts is None: return None
    # Need ATM-ish spot at entry minute — use SPX_open as proxy
    spot_at_entry = spx_open
    # Snap center to nearest available strike
    avail = sorted(puts.index)
    if not avail: return None
    short_k = min(avail, key=lambda x: abs(x - center_strike))
    long_call_k = short_k + wing
    long_put_k  = short_k - wing
    # Need all four strikes in cbbo for puts (calls via parity)
    if short_k not in puts.index or long_put_k not in puts.index or long_call_k not in puts.index:
        return None
    # Credit at entry (mid-based):
    # Sell ATM put @ short_k, sell ATM call @ short_k, buy wing put @ short-wing, buy wing call @ short+wing
    short_put_mid  = float(puts.loc[short_k, "put_mid"])
    short_call_mid = spx_call_via_parity(short_put_mid, spot_at_entry, short_k)
    long_put_mid   = float(puts.loc[long_put_k, "put_mid"])
    long_call_put  = float(puts.loc[long_call_k, "put_mid"])  # put at higher strike, deep ITM
    long_call_mid  = spx_call_via_parity(long_call_put, spot_at_entry, long_call_k)
    credit = (short_put_mid + short_call_mid) - (long_put_mid + long_call_mid)
    # Settlement at expiry (cash):
    # Short put debit: max(0, short_k - spx_close)
    # Short call debit: max(0, spx_close - short_k)
    # Long put receipt: max(0, long_put_k - spx_close)
    # Long call receipt: max(0, spx_close - long_call_k)
    spd = max(0, short_k - spx_close)
    sc  = max(0, spx_close - short_k)
    lpd = max(0, long_put_k - spx_close)
    lcd = max(0, spx_close - long_call_k)
    debit_at_exp = (spd + sc) - (lpd + lcd)
    pnl = (credit - debit_at_exp) * 100
    return {"date":expiry_date, "short":short_k, "wing":wing, "credit":credit,
            "spx_open":spx_open, "spx_close":spx_close,
            "exit_settle":debit_at_exp, "pnl":pnl}

# ===== Backtest =====
def run_spx_pin(wing=50, entry_hh=13, entry_mm=45, definition="combined"):
    rows = []
    files = sorted(OI_DIR.glob("spx_oi_*.parquet"))
    for fp in files:
        ds = fp.stem.split("_")[-1]
        expiry = datetime.strptime(ds, "%Y%m%d").date()
        ts = pd.Timestamp(expiry)
        if ts not in SPX.index: continue
        spot_prev = None  # spot at OI snapshot time = prev close
        prev = SPX.loc[:ts].index[-2] if len(SPX.loc[:ts]) > 1 else None
        if prev is None: continue
        spot_prev = float(SPX.loc[prev, "Close"])

        oi = load_oi("spx", expiry)
        if oi is None or oi.empty: continue

        if definition == "combined":
            center = pin_strike_combined(oi, spot_prev, 5.0)
        elif definition == "gamma_flip":
            center = pin_strike_gamma_flip(oi, spot_prev)
        elif definition == "atm_control":
            center = round(spot_prev / 5) * 5  # no OI used
        else:
            continue
        if center is None: continue

        t = spx_pin_trade(expiry, center, wing, entry_hh, entry_mm)
        if t is None: continue
        t["definition"] = definition
        rows.append(t)
    return pd.DataFrame(rows)

def report(df, label):
    if df.empty: print(f"  {label}: NO trades"); return
    p = df["pnl"]
    w = p[p>0]; l = p[p<0]
    print(f"  {label}: n={len(df)} total=${p.sum():+,.0f} avg=${p.mean():+.0f} "
          f"win={(p>0).mean()*100:.0f}% avg_win=${w.mean() if len(w) else 0:+.0f} "
          f"avg_loss=${l.mean() if len(l) else 0:+.0f} worst=${p.min():+,.0f}")

if __name__ == "__main__":
    print("="*110)
    print("SPX 0DTE PIN-RISK IRON FLY BACKTEST — Fridays 2024-2025, cash-settle at close")
    print("="*110)
    for wing in [25, 50, 100]:
        print(f"\n### Wing = {wing}pt ###")
        for defn in ["atm_control", "combined", "gamma_flip"]:
            df = run_spx_pin(wing=wing, entry_hh=13, entry_mm=45, definition=defn)
            report(df, f"{defn:12s} wing={wing}")
        print()

    # Yearly breakdown for best wing
    print("\n=== YEARLY SPLIT — wing=50, combined-OI pin ===")
    df = run_spx_pin(wing=50, definition="combined")
    if not df.empty:
        df["year"] = pd.to_datetime(df["date"]).dt.year
        for y, grp in df.groupby("year"):
            report(grp, f"  {y}")
