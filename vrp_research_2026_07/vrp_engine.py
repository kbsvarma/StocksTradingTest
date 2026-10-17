"""Single-name short-premium engine over daily EOD chain snapshots.

Chain files: data/chains/<NAME>/<YYYYMMDD>.parquet — cbbo-1m rows 15:54-16:00 ET,
full chain (all expiries/strikes). Canonical mark = last bar <= 15:55; fallback
latest bar in window. OCC symbol embeds expiry/right/strike.

Simulated structures (weekly cadence, Monday entry at EOD marks):
  CSP: sell put at target moneyness, Friday expiry (nearest listed <=7 DTE ahead)
  CC (wheel state): after assignment, hold 100 shares, sell calls; released when
  called away. Fills: SELL@BID, BUY@ASK, $0.65/contract commission.
  PT variants: close at 50%/25% of max profit when the daily 15:55 mark allows.
  Earnings skip variant: no new entry if earnings falls before expiry.
Assignment model: European-ized at expiry vs Friday underlying close (American
early-assignment risk on deep ITM noted as limitation; dividends near zero for
these names). Underlying daily closes from XNAS.ITCH (real).
"""
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np
import pandas as pd

ET = "America/New_York"
CHAINS = Path(__file__).parent / "data/chains"
COMM = 0.65

def parse_chain(fp):
    df = pd.read_parquet(fp)
    if len(df) == 0:
        return None
    t = df.index.tz_convert(ET)
    df = df.assign(minute=t)
    # canonical minute: last bar <= 15:55, else latest available
    cutoff = t.normalize()[0] + pd.Timedelta(hours=15, minutes=55)
    pre = df[df.minute <= cutoff]
    use = pre if len(pre) else df
    last_min = use.minute.max()
    snap = use[use.minute == last_min].copy()
    sym = snap["symbol"].astype(str)
    snap["expiry"] = pd.to_datetime("20" + sym.str.slice(6, 12), format="%Y%m%d")
    snap["right"] = sym.str.slice(12, 13)
    snap["strike"] = sym.str.slice(13, 21).astype(int) / 1000.0
    snap = snap[["expiry", "right", "strike", "bid_px_00", "ask_px_00"]]
    snap.columns = ["expiry", "right", "strike", "bid", "ask"]
    return snap.reset_index(drop=True)

class ChainStore:
    def __init__(self, name):
        self.name = name
        self.files = {f.stem: f for f in (CHAINS / name).glob("*.parquet") if f.stat().st_size > 0}
        self._cache = {}
    def days(self):
        return sorted(self.files)
    def get(self, tag):
        if tag not in self._cache:
            if tag not in self.files:
                return None
            self._cache[tag] = parse_chain(self.files[tag])
            if len(self._cache) > 40:
                self._cache.pop(next(iter(self._cache)))
        return self._cache[tag]

def pick_contract(snap, right, spot, otm_pct, max_dte_days, today):
    """Nearest listed strike to target, nearest expiry within (today, today+max_dte]."""
    if snap is None or len(snap) == 0:
        return None
    side = snap[snap.right == right].copy()
    if right == "P":
        target = spot * (1 - otm_pct)
    else:
        target = spot * (1 + otm_pct)
    horizon = today + pd.Timedelta(days=max_dte_days)
    side = side[(side.expiry > today) & (side.expiry <= horizon)]
    if len(side) == 0:
        return None
    best_exp = side.expiry.min()
    side = side[side.expiry == best_exp]
    side = side[(side.bid > 0) & (side.ask > 0) & (side.ask >= side.bid)]
    if len(side) == 0:
        return None
    side["d"] = (side.strike - target).abs()
    row = side.nsmallest(1, "d").iloc[0]
    return dict(expiry=row.expiry, strike=float(row.strike), bid=float(row.bid),
                ask=float(row.ask), right=right)

def mark_contract(snap, expiry, right, strike):
    if snap is None:
        return None
    m = snap[(snap.expiry == expiry) & (snap.right == right) & (snap.strike == strike)]
    if len(m) == 0:
        return None
    r = m.iloc[0]
    return dict(bid=float(r.bid), ask=float(r.ask))

@dataclass
class WheelResult:
    events: list = field(default_factory=list)

SPLITS = {"MSTR": [("2024-08-08", 10.0)], "NVDA": [("2021-07-20", 4.0), ("2024-06-10", 10.0)],
          "SMCI": [("2024-10-01", 10.0)]}

def run_wheel(name, closes, earnings_dates, otm_pct=0.05, pt=None, skip_earnings=False,
              start="2021-07-01", end="2026-07-02", csp_only=False):
    """Weekly Monday-entry wheel. Returns per-event ledger (premium, assignment,
    share P&L, fees) + daily position record. closes: pd.Series of underlying."""
    store = ChainStore(name)
    days = [d for d in store.days() if start.replace("-", "") <= d <= end.replace("-", "")]
    if not days:
        return None
    cal = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    state = "CASH"          # CASH -> short put | SHARES -> short call
    open_opt = None         # dict(expiry,strike,right,credit)
    shares_basis = None
    n_shares = 100
    splits = [(pd.Timestamp(dt), f) for dt, f in SPLITS.get(name, [])]
    ledger = []
    for i, d in enumerate(cal):
        tag = d.strftime("%Y%m%d")
        snap = store.get(tag)
        spot = closes.get(d, np.nan)
        if np.isnan(spot):
            continue
        # split adjustment on split morning
        for sdt, f in splits:
            if d == sdt or (i > 0 and cal[i-1] < sdt <= d):
                if shares_basis is not None:
                    shares_basis /= f
                    n_shares = int(n_shares * f)
        # force-close any open option the day BEFORE a split takes effect
        nxt = cal[i+1] if i + 1 < len(cal) else None
        if open_opt is not None and nxt is not None and any(d < sdt <= nxt for sdt, f in splits):
            mk = mark_contract(snap, open_opt["expiry"], open_opt["right"], open_opt["strike"])
            close_px = mk["ask"] if mk else max(0.0, (open_opt["strike"] - spot) if open_opt["right"] == "P" else (spot - open_opt["strike"]))
            ledger.append(dict(date=d, name=name, kind="SPLIT_CLOSE",
                               cash=(open_opt["credit"] - close_px) * 100 - COMM))
            open_opt = None
        # 1) manage open option
        if open_opt is not None:
            # PT check
            if pt is not None and d < open_opt["expiry"]:
                mk = mark_contract(snap, open_opt["expiry"], open_opt["right"], open_opt["strike"])
                if mk is not None and mk["ask"] <= open_opt["credit"] * (1 - pt):
                    ledger.append(dict(date=d, name=name, kind=f"PT{int(pt*100)}",
                                       cash=(open_opt["credit"] - mk["ask"]) * 100 - COMM))
                    open_opt = None
            if open_opt is not None and d >= open_opt["expiry"]:
                # settle at expiry vs that day's close (expiry date close)
                s_exp = closes.get(open_opt["expiry"], spot)
                k = open_opt["strike"]
                if open_opt["right"] == "P":
                    if s_exp < k:   # assigned
                        state = "SHARES"; shares_basis = k
                        ledger.append(dict(date=d, name=name, kind="ASSIGN_P", cash=0.0))
                    ledger.append(dict(date=d, name=name, kind="EXP_P",
                                       cash=open_opt["credit"] * 100))
                else:
                    if s_exp > k:   # called away
                        pnl_sh = (k - shares_basis) * n_shares
                        ledger.append(dict(date=d, name=name, kind="CALLED",
                                           cash=pnl_sh))
                        state = "CASH"; shares_basis = None
                    ledger.append(dict(date=d, name=name, kind="EXP_C",
                                       cash=open_opt["credit"] * 100))
                open_opt = None
        # 1b) CSP-only mode: liquidate assigned shares at next session close
        if csp_only and state == "SHARES" and open_opt is None:
            ledger.append(dict(date=d, name=name, kind="LIQ_SHARES",
                               cash=(spot - shares_basis) * n_shares))
            state = "CASH"; shares_basis = None
        # 2) Monday: open new position if flat
        if d.dayofweek == 0 and open_opt is None:
            if skip_earnings and any(d < e <= d + pd.Timedelta(days=7) for e in earnings_dates):
                continue
            right = "P" if state == "CASH" else "C"
            base = spot if state == "CASH" else max(spot, shares_basis * 0.97)
            pc = pick_contract(snap, right, base if right == "P" else spot, otm_pct, 8, d)
            if pc is None or pc["bid"] <= 0.01:
                continue
            credit = pc["bid"]
            ledger.append(dict(date=d, name=name, kind=f"SELL_{right}",
                               cash=-COMM, credit=credit, strike=pc["strike"]))
            open_opt = dict(expiry=pc["expiry"], strike=pc["strike"], right=right,
                            credit=credit)
    # mark-to-market share leg at end
    if state == "SHARES" and shares_basis is not None:
        last = closes.dropna().iloc[-1]
        ledger.append(dict(date=cal[-1], name=name, kind="MTM_SHARES",
                           cash=(last - shares_basis) * n_shares))
    L = pd.DataFrame(ledger)
    return L
