"""Pre-earnings long-vol test per PREREG_EARNVOL.md. IS always; --oos single shot."""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, str(Path(__file__).parent.parent / "event_edge_research"))
from vrp_engine import ChainStore, COMM
from stats_tools import nw_tstat

ET = "America/New_York"
NAMES = ["MARA","MSTR","COIN","SMCI","RIVN","HOOD","SOFI","PLTR","NVDA"]

def closes_map():
    xn = pd.read_parquet("data/equity_dailies_xnas.parquet")
    out = {}
    for sym, g in xn.groupby("symbol"):
        c = g["close"].copy(); c.index = c.index.tz_localize(None).normalize()
        out[sym] = c
    return out

def straddle_quote(snap, spot, min_expiry):
    """ATM straddle at nearest strike, nearest expiry >= min_expiry. Returns dict or None."""
    if snap is None: return None
    s = snap[snap.expiry >= min_expiry]
    if len(s) == 0: return None
    exp = s.expiry.min()
    s = s[s.expiry == exp]
    s = s[(s.bid > 0) & (s.ask > 0) & (s.ask >= s.bid)]
    strikes = sorted(set(s.strike))
    if not strikes: return None
    k = min(strikes, key=lambda x: abs(x - spot))
    c = s[(s.strike == k) & (s.right == "C")]
    p = s[(s.strike == k) & (s.right == "P")]
    if len(c) == 0 or len(p) == 0: return None
    return dict(expiry=exp, strike=k,
                buy=float(c.ask.iloc[0] + p.ask.iloc[0]),
                sell=float(c.bid.iloc[0] + p.bid.iloc[0]),
                mid=float((c.ask.iloc[0]+c.bid.iloc[0])/2 + (p.ask.iloc[0]+p.bid.iloc[0])/2))

def run(entry_off=5, exit_mode="pre"):
    closes = closes_map()
    timed = json.load(open("data/earnings_timed.json"))
    stores = {n: ChainStore(n) for n in NAMES}
    trades = []
    for n in NAMES:
        days = [pd.Timestamp(t) for t in stores[n].days()]
        dset = pd.DatetimeIndex(days)
        for ev in timed[n]:
            E = pd.Timestamp(ev["date"]); sess = ev["session"]
            # trading days at/before E
            pre = dset[dset <= E]
            if len(pre) < entry_off + 1: continue
            # announcement session: AMC on E, BMO on E (pre-open)
            if sess == "AMC":
                exit_day = E                     # pre-event exit: E close
                min_exp = E + pd.Timedelta(days=1)
                post = dset[dset > E]
            else:                                # BMO/UNK conservative
                idx = dset.searchsorted(E)
                exit_day = dset[idx - 1] if idx > 0 else None
                min_exp = E
                post = dset[dset >= E]
            if exit_day is None or exit_day not in dset: continue
            xi = dset.searchsorted(exit_day)
            entry_day = dset[xi - entry_off] if xi >= entry_off else None
            if entry_day is None: continue
            snap_in = stores[n].get(entry_day.strftime("%Y%m%d"))
            spot_in = closes[n].get(entry_day, np.nan)
            if snap_in is None or np.isnan(spot_in): continue
            q_in = straddle_quote(snap_in, spot_in, min_exp)
            if q_in is None: continue
            if exit_mode == "pre":
                out_day = exit_day
            else:                                # through-event reference
                if len(post) == 0: continue
                out_day = post[0]
            snap_out = stores[n].get(out_day.strftime("%Y%m%d"))
            if snap_out is None: continue
            m = snap_out[(snap_out.expiry == q_in["expiry"]) & (snap_out.strike == q_in["strike"])]
            c = m[m.right == "C"]; p = m[m.right == "P"]
            if len(c) == 0 or len(p) == 0: continue
            sell = float(c.bid.iloc[0] + p.bid.iloc[0])
            sell_mid = float((c.bid.iloc[0]+c.ask.iloc[0])/2 + (p.bid.iloc[0]+p.ask.iloc[0])/2)
            pnl = (sell - q_in["buy"]) * 100 - 4 * COMM
            pnl_mid = (sell_mid - q_in["mid"]) * 100 - 4 * COMM
            trades.append(dict(name=n, event=E, entry=entry_day, exit=out_day,
                               strike=q_in["strike"], cost=q_in["buy"] * 100,
                               pnl=pnl, pnl_mid=pnl_mid))
    return pd.DataFrame(trades)

def rep(label, T, col="pnl"):
    x = T[col]
    t, nn = nw_tstat(x.values, lags=2)
    print(f"  {label:<34} n={len(T):<4} mean=${x.mean():+7.1f}  t={t:+5.2f}  "
          f"%pos={(x>0).mean()*100:3.0f}  total=${x.sum():+9.0f}  medcost=${T['cost'].median():,.0f}")

def main(oos=False):
    for entry_off in (5, 3):
        for mode in ("pre", "through"):
            T = run(entry_off, mode)
            T = T.sort_values("event")
            is_t = T[T.event < pd.Timestamp("2025-01-01")]
            oos_t = T[T.event >= pd.Timestamp("2025-01-01")]
            tag = f"E-{entry_off} {'PRE-event' if mode=='pre' else 'THROUGH-event'}"
            print(f"\n=== {tag} ===")
            rep("IS  (conservative fills)", is_t)
            rep("IS  (mid fills)", is_t, "pnl_mid")
            pos_names = (is_t.groupby("name")["pnl"].mean() > 0)
            print(f"  IS breadth: {pos_names.sum()}/{pos_names.count()} names positive")
            if oos:
                rep("OOS *single shot*", oos_t)

if __name__ == "__main__":
    main("--oos" in sys.argv)
