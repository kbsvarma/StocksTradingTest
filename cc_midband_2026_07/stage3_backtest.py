"""Stage-3: CC overlay (user's structure: hold shares, sell weekly calls, rebuy
next session if called) + wheel variant, on the 8 finalists, real quotes.
Cells per name (prereg): structure(overlay, wheel) x otm(2.5%, 5%), skip-earnings ON.
IS = first 70% of window; OOS = last 30% single shot. Edge = strategy - B&H.
"""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "vrp_research_2026_07"))
import vrp_engine
vrp_engine.CHAINS = Path(__file__).parent / "data/chains"
from vrp_engine import ChainStore, pick_contract, mark_contract, COMM, run_wheel
import vrp_engine as VE

NAMES = ["NET", "ANET", "AVGO", "UAL", "DAL", "CCL", "NVDA", "UBER"]

def load_closes():
    eq = pd.read_parquet("data/finalist_dailies.parquet")
    out = {}
    for sym, g in eq.groupby("symbol"):
        c = g["close"].sort_index(); c.index = c.index.tz_localize(None).normalize()
        out[sym] = c
    return out

def detect_splits(c):
    ratio = c.shift(1) / c
    ev = []
    for i in np.where((ratio.values > 1.8) | (ratio.values < 0.55))[0]:
        rv = float(ratio.iloc[i])
        f = float(round(rv)) if rv > 1.8 else 1.0 / round(1 / rv)
        ev.append((c.index[i], f))
    return ev

def bh_value_path(c, splits):
    """Daily $ value of holding 100 initial shares, share count multiplying at
    splits (same path the overlay's share leg follows)."""
    n = 100.0
    vals = []
    for i, (d, px) in enumerate(c.items()):
        for sdt, f in splits:
            if i > 0 and c.index[i - 1] < sdt <= d:
                n *= f
        vals.append(n * float(px))
    return pd.Series(vals, index=c.index)

def cc_overlay(name, closes, earnings, splits, otm=0.025):
    """Hold 100sh from first chain day; every Monday sell OTM weekly call (skip
    earnings weeks); assignment -> deliver at strike, rebuy next session close."""
    store = ChainStore(name)
    days = [pd.Timestamp(t) for t in store.days()]
    cal = pd.DatetimeIndex([d for d in days if d in closes.index])
    if len(cal) < 100:
        return None
    basis = float(closes[cal[0]]); n_sh = 100
    have_shares = True; rebuy_pending = False
    open_opt = None
    ledger = [dict(date=cal[0], kind="BUY_INIT", cash=0.0)]
    for i, d in enumerate(cal):
        spot = float(closes[d])
        # split adjust
        for sdt, f in splits:
            if i > 0 and cal[i - 1] < sdt <= d:
                basis /= f; n_sh = int(n_sh * f)
                if open_opt is not None:
                    # force-closed yesterday in live; approximate: cancel at intrinsic
                    open_opt = None
        # rebuy morning after assignment (user rule: next session close)
        if rebuy_pending:
            ledger.append(dict(date=d, kind="REBUY", cash=0.0))
            basis = spot; have_shares = True; rebuy_pending = False
        # settle option
        if open_opt is not None and d >= open_opt["expiry"]:
            s_exp = float(closes.get(open_opt["expiry"], spot))
            k = open_opt["strike"]
            ledger.append(dict(date=d, kind="EXP_C", cash=open_opt["credit"] * (n_sh)))
            if s_exp > k:  # called away
                ledger.append(dict(date=d, kind="CALLED", cash=(k - basis) * n_sh))
                have_shares = False; rebuy_pending = True
            open_opt = None
        # Monday: sell call if holding and no open option
        if d.dayofweek == 0 and have_shares and open_opt is None:
            if any(d < pd.Timestamp(e) <= d + pd.Timedelta(days=7) for e in earnings):
                continue
            snap = store.get(d.strftime("%Y%m%d"))
            pc = pick_contract(snap, "C", spot, otm, 8, d)
            if pc is None or pc["bid"] <= 0.01:
                continue
            ledger.append(dict(date=d, kind="SELL_C", cash=-COMM))
            open_opt = dict(expiry=pc["expiry"], strike=pc["strike"], credit=pc["bid"])
    # close out: MTM shares vs basis
    last = float(closes[cal[-1]])
    if have_shares:
        ledger.append(dict(date=cal[-1], kind="MTM", cash=(last - basis) * n_sh))
    L = pd.DataFrame(ledger)
    return L

def stats(L, is_end):
    cash = L.set_index("date")["cash"]
    is_p = cash[cash.index <= is_end]; oos = cash[cash.index > is_end]
    m = cash.groupby(cash.index.to_period("M")).sum()
    return dict(total=cash.sum(), is_total=is_p.sum(), oos_total=oos.sum(),
                med_month=m.median(), worst_month=m.min())

def main():
    closes = load_closes()
    earnings = {k: [pd.Timestamp(x) for x in v] for k, v in json.load(open("data/finalist_earnings.json")).items()}
    rows = []
    for name in NAMES:
        c = closes[name]
        splits = detect_splits(c)
        # window = chain availability
        store = ChainStore(name)
        days = [pd.Timestamp(t) for t in store.days()]
        w0, w1 = days[0], days[-1]
        cwin = c[(c.index >= w0) & (c.index <= w1)]
        is_end = cwin.index[int(len(cwin) * 0.7)]
        vp = bh_value_path(cwin, splits)
        bh_total = float(vp.iloc[-1] - vp.iloc[0])
        v_is_end = float(vp[vp.index <= is_end].iloc[-1])
        bh_is = v_is_end - float(vp.iloc[0])
        bh_oos = float(vp.iloc[-1]) - v_is_end
        for otm in (0.025, 0.05):
            L = cc_overlay(name, cwin, earnings[name], splits, otm)
            if L is None: continue
            st = stats(L, is_end)
            rows.append(dict(name=name, structure="overlay", otm=otm, **st,
                             bh=bh_total, bh_is=bh_is, bh_oos=bh_oos,
                             edge_is=st["is_total"] - bh_is, edge_oos=st["oos_total"] - bh_oos))
        print(f"{name} done (splits: {[(str(s[0].date()), s[1]) for s in splits]})", flush=True)
    R = pd.DataFrame(rows)
    R.to_parquet("data/stage3_results.parquet")
    cols = ["name","otm","total","bh","is_total","bh_is","edge_is","oos_total","bh_oos","edge_oos","med_month","worst_month"]
    print(R[cols].round(0).to_string(index=False))

if __name__ == "__main__":
    main()
