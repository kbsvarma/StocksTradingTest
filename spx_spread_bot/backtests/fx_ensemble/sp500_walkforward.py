"""S&P 500 stocks — same ensemble framework, adapted for equities.

Adaptations vs FX:
  - TP/SL in ATR-multiples (not fixed pips) — scales across $5..$700 stocks.
  - Cost = bps each way (liquid large-cap spread+slippage proxy).
  - Data split/dividend ADJUSTED (auto_adjust).
  - Report LONG vs SHORT expectancy separately -> isolates real signal edge
    from mere equity-drift capture (the key stock-specific honesty check).

Same anti-overfit walk-forward: pooled global params, optimize IS, measure
untouched OOS, roll across 20 years.
"""
import numpy as np, pandas as pd
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
from fx_harness import build_signal

DATA = Path(__file__).parent / "sp500_data"
STOCKS = sorted(p.stem for p in DATA.glob("*.parquet"))
COST_BPS = 5.0  # each way (half-spread + slippage + commission proxy), conservative

FRAMES = {}
for s in STOCKS:
    d = pd.read_parquet(DATA / f"{s}.parquet")
    d = d[(d[["Open","High","Low","Close"]] > 0).all(axis=1)].copy()
    if len(d) > 300:
        # precompute ATR(14) in price terms
        tr = pd.concat([d["High"]-d["Low"],
                        (d["High"]-d["Close"].shift(1)).abs(),
                        (d["Low"]-d["Close"].shift(1)).abs()], axis=1).max(axis=1)
        d["ATR"] = tr.rolling(14).mean()
        FRAMES[s] = d


def backtest_window(stock, df, params, k_tp, k_sl, max_hold, lo, hi):
    sig = build_signal(df, params).values
    O=df["Open"].values; H=df["High"].values; L=df["Low"].values; C=df["Close"].values
    ATR=df["ATR"].values
    cost = COST_BPS/10000.0
    n=len(df); longs=[]; shorts=[]
    i=max(lo,60); end=min(hi,n-1)
    while i<end:
        s=sig[i]
        if s==0 or np.isnan(ATR[i]) or ATR[i]<=0: i+=1; continue
        ei=i+1
        entry=O[ei]*(1+cost*s)   # pay cost in adverse direction
        atr=ATR[i]
        if s==1: tp=entry+k_tp*atr; sl=entry-k_sl*atr
        else:    tp=entry-k_tp*atr; sl=entry+k_sl*atr
        ex=None
        for j in range(ei,min(ei+max_hold,n)):
            if s==1:
                if L[j]<=sl: ex=sl; break
                if H[j]>=tp: ex=tp; break
            else:
                if H[j]>=sl: ex=sl; break
                if L[j]<=tp: ex=tp; break
        if ex is None: ex=C[min(ei+max_hold-1,n-1)]
        ex=ex*(1-cost*s)  # cost on exit
        ret=((ex-entry)/entry)*s*100  # % return per trade
        (longs if s==1 else shorts).append(ret)
        i=j+1
    return longs, shorts


def pooled(params,k_tp,k_sl,max_hold,lo_f,hi_f):
    L=[];S=[]
    for st in STOCKS:
        if st not in FRAMES: continue
        df=FRAMES[st]; n=len(df)
        lo=int(n*lo_f); hi=int(n*hi_f)
        l,s=backtest_window(st,df,params,k_tp,k_sl,max_hold,lo,hi)
        L+=l; S+=s
    allr=L+S
    if len(allr)<50: return None
    return dict(exp=float(np.mean(allr)), n=len(allr),
                long_exp=float(np.mean(L)) if L else 0, long_n=len(L),
                short_exp=float(np.mean(S)) if S else 0, short_n=len(S))


GRID=[]
for don_n in (10,20,40):
    for mom in ((10,40),(20,60)):
        for mr_z in (1.5,2.0):
            for vote in (1,2):
                for k_tp in (1.5,2.0,3.0):
                    for k_sl in (2.0,3.0):
                        GRID.append(dict(params=dict(don_n=don_n,mom_fast=mom[0],mom_slow=mom[1],
                            mr_n=20,mr_z=mr_z,atr_n=14,vol_lo=0.2,vol_hi=0.95,vote_min=vote),
                            k_tp=k_tp,k_sl=k_sl))
print(f"Stocks: {len(FRAMES)} | grid {len(GRID)} | cost {COST_BPS}bps each way",flush=True)

U=1/11; windows=[]; f=0.0
while f+6*U<=1.0001:
    windows.append((f,f+4*U,f+4*U,f+6*U)); f+=2*U

print("\n"+"="*86)
print("S&P 500 ENSEMBLE — walk-forward (ATR exits, adjusted data, drift check)")
print("="*86)
print(f"{'Win':<5}{'IS exp%':<10}{'OOS exp%':<11}{'OOS long%':<11}{'OOS short%':<12}{'OOS N':<7}")
print("-"*70)
oos_all=[]
for wi,(islo,ishi,ooslo,ooshi) in enumerate(windows):
    best=None
    for g in GRID:
        r=pooled(g["params"],g["k_tp"],g["k_sl"],10,islo,ishi)
        if r is None or r["n"]<200: continue
        if best is None or r["exp"]>best[0]["exp"]: best=(r,g)
    if best is None: continue
    r_is,g=best
    r_oos=pooled(g["params"],g["k_tp"],g["k_sl"],10,ooslo,ooshi)
    if r_oos is None: continue
    oos_all.append(r_oos)
    print(f"{wi:<5}{r_is['exp']:<+9.3f}{r_oos['exp']:<+10.3f}"
          f"{r_oos['long_exp']:<+10.3f}{r_oos['short_exp']:<+11.3f}{r_oos['n']:<7}",flush=True)

if oos_all:
    tn=sum(r["n"] for r in oos_all)
    wexp=sum(r["exp"]*r["n"] for r in oos_all)/tn
    ln=sum(r["long_n"] for r in oos_all); sn=sum(r["short_n"] for r in oos_all)
    lexp=sum(r["long_exp"]*r["long_n"] for r in oos_all)/ln if ln else 0
    sexp=sum(r["short_exp"]*r["short_n"] for r in oos_all)/sn if sn else 0
    print("-"*70)
    print(f"\nPOOLED OOS expectancy: {wexp:+.3f}% per trade over {tn} trades")
    print(f"  LONG  trades: {lexp:+.3f}% over {ln}")
    print(f"  SHORT trades: {sexp:+.3f}% over {sn}")
    print(f"  OOS windows positive: {sum(1 for r in oos_all if r['exp']>0)}/{len(oos_all)}")
    print("\nHONEST READ:")
    if lexp>0.05 and sexp>0.05:
        print("  Both long AND short positive OOS -> genuine signal edge (not just drift). Dig deeper.")
    elif wexp>0 and lexp>0 and sexp<=0:
        print("  Longs positive, shorts negative -> this is DRIFT CAPTURE, not signal edge.")
        print("  A long-only index hold likely does as well or better with less effort.")
    elif wexp<=0:
        print("  OOS <= 0 -> no edge from these signals on equities either (as on FX).")
    else:
        print("  Marginal/mixed -> fragile; survivorship bias likely flatters even this.")
