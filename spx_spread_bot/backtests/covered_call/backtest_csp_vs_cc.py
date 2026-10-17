"""CSP vs CC comparison + the wheel, on weekly data.

For each Monday, pick the put ~5% OTM and the call ~5% OTM (symmetric).
  - CSP premium = put bid (sell put)
  - CC  premium = call bid (sell call)
  - Skew advantage = CSP premium - CC premium at equal % OTM
Friday settlement via yfinance close:
  - CSP assigned if close < put strike (buy shares; cost basis = strike - premium)
  - CC  assigned if close > call strike (shares called; capped)
Reports per-ticker premium yield, assignment rates, skew.
"""
import argparse, re
from pathlib import Path
import pandas as pd
import yfinance as yf

DATA = Path(__file__).parent / "data"
RX = re.compile(r"\s(\d{6})([PC])(\d{8})$")
def parse(s):
    m=RX.search(s.strip())
    if not m: return None
    return m.group(2), int(m.group(3))/1000  # side, strike

def nearest_otm(piv_side, spot, pct, is_put):
    """Find strike closest to spot*(1±pct) that exists."""
    target = spot*(1-pct) if is_put else spot*(1+pct)
    ks = list(piv_side.index)
    if not ks: return None
    return min(ks, key=lambda k: abs(k-target))

def run(ticker, otm=0.05):
    fp = DATA / f"{ticker.lower()}_csp_weekly.parquet"
    if not fp.exists(): print(f"  {ticker}: no data"); return None
    df = pd.read_parquet(fp)
    df = df[(df["bid_px_00"]>0)].copy()
    parsed = df["symbol"].map(parse)
    df = df[parsed.notna()].copy()
    df["side"]=parsed[parsed.notna()].map(lambda x:x[0])
    df["strike"]=parsed[parsed.notna()].map(lambda x:x[1])

    t=yf.Ticker(ticker)
    hist=t.history(start="2024-01-01",end="2026-05-23",auto_adjust=False)
    hist.index=pd.to_datetime(hist.index).tz_localize(None)

    csp_prem=0.0; cc_prem=0.0; csp_assign=0; cc_assign=0; n=0
    skews=[]
    csp_pnl=0.0; cc_pnl=0.0
    for mon, g in df.groupby("monday"):
        spot=float(g["spot"].iloc[0]); fri=pd.Timestamp(g["friday"].iloc[0])
        puts=g[g["side"]=="P"].drop_duplicates("strike").set_index("strike")["bid_px_00"]
        calls=g[g["side"]=="C"].drop_duplicates("strike").set_index("strike")["bid_px_00"]
        if puts.empty or calls.empty: continue
        pk=nearest_otm(puts,spot,otm,True); ck=nearest_otm(calls,spot,otm,False)
        if pk is None or ck is None: continue
        put_bid=float(puts.loc[pk]); call_bid=float(calls.loc[ck])
        # Friday close
        fi=hist.index.get_indexer([fri],method="ffill")[0]
        if fi<0: continue
        fclose=float(hist.iloc[fi]["Close"])
        n+=1
        csp_prem+=put_bid*100; cc_prem+=call_bid*100
        skews.append((put_bid-call_bid)*100)
        # CSP P&L: keep premium; if assigned (close<pk), also mark-to-market loss (close-pk)*100 (paper)
        csp_pnl += put_bid*100 + (min(0,(fclose-pk))*100)
        # CC P&L: keep premium; if assigned (close>ck), give up (close-ck)*100
        cc_pnl  += call_bid*100 + (min(0,(ck-fclose))*100)
        if fclose<pk: csp_assign+=1
        if fclose>ck: cc_assign+=1
    if n==0: print(f"  {ticker}: no valid weeks"); return None
    return {"ticker":ticker,"n":n,
            "csp_prem":csp_prem,"cc_prem":cc_prem,
            "skew_avg":sum(skews)/len(skews),"skew_total":sum(skews),
            "csp_assign":csp_assign/n*100,"cc_assign":cc_assign/n*100,
            "csp_pnl":csp_pnl,"cc_pnl":cc_pnl}

if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--tickers",default="WFC,OXY,GILD,MO,T")
    ap.add_argument("--otm",type=float,default=0.05); args=ap.parse_args()
    print("="*100)
    print(f"CSP vs CC — sell put/call ~{args.otm*100:.0f}% OTM weekly, 2024-2026, premium at BID")
    print("="*100)
    print(f"\n{'TKR':<6}{'wks':>5}{'CSP prem':>11}{'CC prem':>11}{'skew/wk':>9}{'CSPassign%':>11}{'CCassign%':>10}{'CSP P&L':>11}{'CC P&L':>11}")
    rows=[]
    for t in args.tickers.split(","):
        r=run(t.strip(),args.otm)
        if r: rows.append(r); print(f"{r['ticker']:<6}{r['n']:>5}{r['csp_prem']:>+11,.0f}{r['cc_prem']:>+11,.0f}{r['skew_avg']:>+9.1f}{r['csp_assign']:>10.0f}%{r['cc_assign']:>9.0f}%{r['csp_pnl']:>+11,.0f}{r['cc_pnl']:>+11,.0f}")
    if rows:
        print("\n=== TOTALS ===")
        tc=sum(r['csp_prem'] for r in rows); tcc=sum(r['cc_prem'] for r in rows)
        print(f"  Total CSP premium collected: ${tc:+,.0f}")
        print(f"  Total CC  premium collected: ${tcc:+,.0f}")
        print(f"  Skew advantage (CSP-CC):     ${tc-tcc:+,.0f}  ({(tc/tcc-1)*100:+.0f}% more premium selling puts)")
        print(f"  Total CSP P&L (incl paper assignment): ${sum(r['csp_pnl'] for r in rows):+,.0f}")
        print(f"  Total CC  P&L (incl capped upside):    ${sum(r['cc_pnl'] for r in rows):+,.0f}")
